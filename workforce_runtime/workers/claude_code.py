from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import Artifact, ReportContract, TaskContract, UsageCost
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.base import RuntimeContext, WorkerRun
from workforce_runtime.workers.process_runner import run_process_streaming


class ClaudeCodeWorker:
    def __init__(
        self,
        *,
        claude_executable: str = "claude",
        timeout_seconds: int | None = None,
    ) -> None:
        self.claude_executable = claude_executable
        self.timeout_seconds = timeout_seconds
        self._runs: dict[str, WorkerRun] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def declare_capabilities(self) -> list[str]:
        return ["claude_code", "code_editing", "test_execution", "reporting"]

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        run_id = f"run_{uuid4().hex[:12]}"
        file_store = FileStore(runtime_context.workspace)
        task_dir = file_store.task_artifact_dir(task.task_id)
        task_contract_path = task_dir / "task_contract.json"
        final_message_path = task_dir / "claude-final.md"
        task_contract_path.write_text(task.model_dump_json(indent=2))

        prompt = self._build_prompt(task, runtime_context)
        runtime_context.runtime.update_task_status(
            task.task_id,
            status="in_progress",
            actor_id=runtime_context.agent_id,
        )

        command = [
            self.claude_executable,
            "-p",
            prompt,
            "--output-format",
            "json",
        ]

        streamed = run_process_streaming(
            command=command,
            cwd=runtime_context.workspace,
            env=None,
            timeout_seconds=self.timeout_seconds,
            runtime=runtime_context.runtime,
            file_store=file_store,
            run_id=run_id,
            task_id=task.task_id,
            agent_id=runtime_context.agent_id,
            timeout_message="claude worker timed out",
        )
        returncode = streamed.returncode
        stdout_path = streamed.stdout_path
        stderr_path = streamed.stderr_path

        final_text, usage = self._extract_result(stdout_path)
        final_message_path.write_text(final_text)
        diff_path = self._capture_git_diff(file_store, runtime_context.workspace, task.task_id)
        self._usage[run_id] = usage

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
        )
        self._runs[run_id] = run
        return run

    def collect_artifacts(self, run_id: str) -> list[Path]:
        run = self._runs[run_id]
        return sorted(run.stdout_path.parent.iterdir())

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
