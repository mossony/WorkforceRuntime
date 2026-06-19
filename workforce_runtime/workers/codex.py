from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from workforce_runtime.config import format_model_context_note
from workforce_runtime.core import Artifact, ReportContract, TaskContract, UsageCost
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.base import RuntimeContext, WorkerRun
from workforce_runtime.workers.process_runner import run_process_streaming


class CodexWorker:
    def __init__(
        self,
        *,
        codex_executable: str = "codex",
        profile: str = "workforce-openrouter",
        timeout_seconds: int | None = None,
    ) -> None:
        self.codex_executable = codex_executable
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self._runs: dict[str, WorkerRun] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def declare_capabilities(self) -> list[str]:
        return ["codex", "code_editing", "test_execution", "reporting"]

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        run_id = f"run_{uuid4().hex[:12]}"
        file_store = FileStore(runtime_context.workspace)
        task_dir = file_store.task_artifact_dir(task.task_id)
        task_contract_path = task_dir / "task_contract.json"
        final_message_path = task_dir / "codex-final.md"
        task_contract_path.write_text(task.model_dump_json(indent=2))

        prompt = self._build_prompt(task, runtime_context)
        runtime_context.runtime.update_task_status(
            task.task_id,
            status="in_progress",
            actor_id=runtime_context.agent_id,
        )

        command = [
            self.codex_executable,
            "--profile",
            self.profile,
            "-a",
            "never",
            "-s",
            "workspace-write",
            "-C",
            str(runtime_context.workspace),
            "exec",
            "--json",
            "--output-last-message",
            str(final_message_path),
            prompt,
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
            timeout_message="codex worker timed out",
        )
        returncode = streamed.returncode
        stdout_path = streamed.stdout_path
        stderr_path = streamed.stderr_path

        diff_path = self._capture_git_diff(file_store, runtime_context.workspace, task.task_id)
        usage = self._extract_usage(stdout_path)
        self._usage[run_id] = usage

        final_text = final_message_path.read_text() if final_message_path.exists() else ""
        final_status = "completed" if returncode == 0 and final_text.strip() else "failed"

        if final_message_path.exists():
            runtime_context.runtime.register_artifact(
                Artifact(
                    artifact_id=f"artifact_{uuid4().hex[:12]}",
                    task_id=task.task_id,
                    agent_id=runtime_context.agent_id,
                    type="codex_final_message",
                    path=str(final_message_path),
                    description="Final Codex worker message.",
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
                    description="Git diff after Codex worker run.",
                )
            )

        runtime_context.runtime.register_report(
            ReportContract(
                report_id=f"report_{uuid4().hex[:12]}",
                from_agent_id=runtime_context.agent_id,
                to_agent_id=runtime_context.manager_id or "human",
                task_id=task.task_id,
                summary=final_text.strip() or "Codex worker did not produce a final message.",
                status=final_status,
                work_done=["Ran Codex CLI worker", "Captured stdout, stderr, final message, and git diff"],
                evidence=[
                    {"type": "stdout", "path": str(stdout_path)},
                    {"type": "stderr", "path": str(stderr_path)},
                ],
                risks=[] if returncode == 0 else ["Codex process exited with a nonzero status."],
                blockers=[],
                confidence=0.75 if returncode == 0 else 0.2,
                cost=UsageCost(
                    tokens_used=usage["input_tokens"] + usage["output_tokens"],
                    runtime_seconds=0,
                    tool_calls=0,
                ),
                next_action="Ready for manager review." if final_status == "completed" else "Inspect Codex logs.",
                requires_decision=False,
                alignment_check="Codex received the structured task contract.",
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
        agent = runtime_context.runtime.get_agent(runtime_context.agent_id)
        model = agent.model if agent is not None else ""
        return f"""You are an AI worker inside Workforce Runtime.

Your agent id is: {runtime_context.agent_id}
Your manager is: {runtime_context.manager_id or "human"}
Your assigned model is: {model or "runtime default"}.
{format_model_context_note(model)}
Your assigned task is:

{task.model_dump_json(indent=2)}

You must work only within the given workspace.
You must respect all constraints.

When Workforce Runtime MCP tools are available, use them to report progress and submit artifacts.

When you finish, provide a concise final report with:
- summary
- status
- work_done
- evidence
- risks
- blockers
- confidence
- cost estimate
- next_action
- whether a decision is required

Do not claim completion without producing a final report or artifact.
"""

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

    def _extract_usage(self, stdout_path: Path) -> dict[str, int]:
        usage = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        for line in stdout_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                event_usage = event.get("usage") or {}
                for key in usage:
                    usage[key] = int(event_usage.get(key) or 0)
        return usage
