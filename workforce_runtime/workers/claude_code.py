from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from workforce_runtime.config import load_runtime_config
from workforce_runtime.core import Artifact, ReportContract, TaskContract, UsageCost
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.base import RuntimeContext, WorkerRun
from workforce_runtime.workers.env import worker_process_env
from workforce_runtime.workers.mcp_config import claude_mcp_config_json
from workforce_runtime.workers.process_runner import run_process_streaming
from workforce_runtime.workers.sandbox import apply_process_sandbox, record_sandbox_application, worker_extra_args
from workforce_runtime.workers.session_resume import (
    consume_queued_steers_for_resume,
    extract_claude_session_id,
)


class ClaudeCodeWorker:
    def __init__(
        self,
        *,
        claude_executable: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        config = load_runtime_config().get("workers", {}).get("claude_code", {})
        self.claude_executable = claude_executable or str(config.get("executable") or "claude")
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else config.get("timeout_seconds")
        self._runs: dict[str, WorkerRun] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def declare_capabilities(self) -> list[str]:
        return ["claude_code", "code_editing", "test_execution", "reporting"]

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        run_id = f"run_{uuid4().hex[:12]}"
        workspace = runtime_context.workspace.resolve()
        file_store = FileStore(workspace)
        task_dir = file_store.agent_task_run_dir(
            agent_id=runtime_context.agent_id,
            task_id=task.task_id,
            run_id=run_id,
        )
        task_contract_path = task_dir / "task_contract.json"
        final_message_path = task_dir / "claude-final.md"
        task_contract_path.write_text(task.model_dump_json(indent=2))
        runtime_context.runtime.materialize_agent_skills(
            agent_id=runtime_context.agent_id,
            worker_type="claude_code",
            workspace=workspace,
            task_id=task.task_id,
            run_id=run_id,
            actor_id=runtime_context.agent_id,
        )

        prompt = self._build_prompt(task, runtime_context)
        runtime_context.runtime.update_task_status(
            task.task_id,
            status="in_progress",
            actor_id=runtime_context.agent_id,
        )

        command = [
            self.claude_executable,
            *worker_extra_args("claude_code"),
            "--mcp-config",
            claude_mcp_config_json(runtime_context),
            "-p",
            prompt,
            "--output-format",
            "json",
        ]
        sandboxed = apply_process_sandbox(command, worker_type="claude_code", workspace=workspace)
        record_sandbox_application(
            runtime_context.runtime,
            application=sandboxed,
            run_id=run_id,
            task_id=task.task_id,
            agent_id=runtime_context.agent_id,
        )

        streamed = run_process_streaming(
            command=sandboxed.command,
            cwd=workspace,
            env=worker_process_env(
                runtime_context,
                run_id=run_id,
                task=task,
                task_contract_path=task_contract_path,
                run_dir=task_dir,
            ),
            timeout_seconds=self.timeout_seconds,
            runtime=runtime_context.runtime,
            file_store=file_store,
            run_id=run_id,
            task_id=task.task_id,
            agent_id=runtime_context.agent_id,
            timeout_message="claude worker timed out",
            run_dir=task_dir,
        )
        returncode = streamed.returncode
        stdout_path = streamed.stdout_path
        stderr_path = streamed.stderr_path

        final_text, usage = self._extract_result(stdout_path)
        final_message_path.write_text(final_text)
        self._usage[run_id] = usage
        provider_session_id = extract_claude_session_id(stdout_path.read_text())
        resume_command = f"claude -p --resume {provider_session_id}" if provider_session_id else ""
        session_metadata: dict[str, object] = {
            "executable": self.claude_executable,
            "execution_mode": sandboxed.metadata.get("execution_mode", "full_access"),
            "sandbox_applied": sandboxed.applied,
            "sandbox_command_prefix": sandboxed.metadata.get("sandbox_command_prefix", []),
            "sandbox_settings_path": sandboxed.metadata.get("sandbox_settings_path", ""),
            "timeout_seconds": self.timeout_seconds or "",
        }
        if provider_session_id:
            runtime_context.runtime.record_provider_session(
                provider="claude_code",
                provider_session_id=provider_session_id,
                run_id=run_id,
                task_id=task.task_id,
                actor_id=runtime_context.agent_id,
                workspace=str(workspace),
                resume_command=resume_command,
                worker_type="claude_code",
                metadata=session_metadata,
            )
            queued_results = consume_queued_steers_for_resume(
                runtime_context.runtime,
                agent_id=runtime_context.agent_id,
                task_id=task.task_id,
                provider_session_id=provider_session_id,
                workspace=workspace,
                metadata=session_metadata,
            )
            if queued_results and queued_results[-1].final_text.strip():
                final_text = queued_results[-1].final_text
                final_message_path.write_text(final_text)

        diff_path = self._capture_git_diff(file_store, workspace, task.task_id)
        final_status = "completed" if returncode == 0 and final_text.strip() else "failed"
        runtime_context.runtime.register_artifact(
            Artifact(
                artifact_id=f"artifact_{uuid4().hex[:12]}",
                task_id=task.task_id,
                agent_id=runtime_context.agent_id,
                type="claude_final_message",
                path=str(final_message_path),
                description="Final Claude Code worker message.",
            )
        )
        if diff_path is not None:
            runtime_context.runtime.register_artifact(
                Artifact(
                    artifact_id=f"artifact_{uuid4().hex[:12]}",
                    task_id=task.task_id,
                    agent_id=runtime_context.agent_id,
                    type="git_diff",
                    path=str(diff_path),
                    description="Git diff after Claude Code worker run.",
                )
            )

        runtime_context.runtime.register_report(
            ReportContract(
                report_id=f"report_{uuid4().hex[:12]}",
                from_agent_id=runtime_context.agent_id,
                to_agent_id=runtime_context.manager_id or "human",
                task_id=task.task_id,
                summary=final_text.strip() or "Claude Code worker did not produce a final message.",
                status=final_status,
                work_done=["Ran Claude Code CLI worker", "Captured stdout, stderr, final message, and git diff"],
                evidence=[
                    {"type": "stdout", "path": str(stdout_path)},
                    {"type": "stderr", "path": str(stderr_path)},
                ],
                risks=[] if returncode == 0 else ["Claude process exited with a nonzero status."],
                blockers=[],
                confidence=0.75 if returncode == 0 else 0.2,
                cost=UsageCost(
                    tokens_used=usage["input_tokens"] + usage["output_tokens"],
                    runtime_seconds=0,
                    tool_calls=0,
                ),
                next_action="Ready for manager review." if final_status == "completed" else "Inspect Claude logs.",
                requires_decision=False,
                alignment_check="Claude Code received the structured task contract.",
            )
        )
        runtime_context.runtime.update_task_status(
            task.task_id,
            status=final_status,
            actor_id=runtime_context.agent_id,
        )

        run = WorkerRun(
            run_id=run_id,
            task_id=task.task_id,
            returncode=returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            task_contract_path=task_contract_path,
            provider_session_id=provider_session_id,
            resume_command=resume_command,
        )
        self._runs[run_id] = run
        return run

    def collect_artifacts(self, run_id: str) -> list[Path]:
        run = self._runs[run_id]
        paths = list(run.stdout_path.parent.iterdir())
        legacy_dir = FileStore(FileStore.workspace_from_run_file(run.stdout_path)).task_artifact_dir(run.task_id)
        if legacy_dir.exists():
            paths.extend(legacy_dir.iterdir())
        return sorted(set(paths))

    def stop_task(self, run_id: str) -> None:
        if run_id not in self._runs:
            raise KeyError(f"run not found: {run_id}")

    def get_usage(self, run_id: str) -> dict[str, int]:
        return self._usage[run_id]

    def _build_prompt(self, task: TaskContract, runtime_context: RuntimeContext) -> str:
        return f"""You are an AI worker inside Workforce Runtime.

Your agent id is: {runtime_context.agent_id}
Your manager is: {runtime_context.manager_id or "human"}
Your assigned task is:

{task.model_dump_json(indent=2)}

You must work only within the given workspace.
You must respect all constraints.

When Workforce Runtime MCP tools are available, use them to report progress and submit artifacts.
If this is a report-review task, inspect the report and artifacts, then call review_report() with an explicit decision.
When you finish, provide a concise final report with summary, status, work done, evidence, risks, blockers, confidence, cost estimate, next action, and whether a decision is required.
"""

    def _extract_result(self, stdout_path: Path) -> tuple[str, dict[str, int]]:
        usage = {"input_tokens": 0, "output_tokens": 0}
        text = stdout_path.read_text()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text, usage

        final_text = str(payload.get("result") or payload.get("content") or payload.get("text") or "")
        usage_payload = payload.get("usage") or {}
        if isinstance(usage_payload, dict):
            usage["input_tokens"] = int(usage_payload.get("input_tokens") or 0)
            usage["output_tokens"] = int(usage_payload.get("output_tokens") or 0)
        return final_text, usage

    def _capture_git_diff(self, file_store: FileStore, workspace: Path, task_id: str) -> Path | None:
        result = subprocess.run(
            ["git", "diff", "--"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return file_store.save_git_diff(task_id, result.stdout)
