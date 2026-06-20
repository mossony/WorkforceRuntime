from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import TaskContract
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.base import RuntimeContext, WorkerRun
from workforce_runtime.workers.process_runner import run_process_streaming


class GenericCLIWorker:
    def __init__(self, command: list[str], *, timeout_seconds: int | None = None) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self._runs: dict[str, WorkerRun] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def declare_capabilities(self) -> list[str]:
        return ["generic_cli", "stdout_capture", "stderr_capture", "task_json"]

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        run_id = f"run_{uuid4().hex[:12]}"
        file_store = FileStore(runtime_context.workspace)
        task_dir = file_store.agent_task_run_dir(
            agent_id=runtime_context.agent_id,
            task_id=task.task_id,
            run_id=run_id,
        )
        task_contract_path = task_dir / "task_contract.json"
        task_contract_path.write_text(task.model_dump_json(indent=2))

        runtime_context.runtime.update_task_status(
            task.task_id,
            status="in_progress",
            actor_id=runtime_context.agent_id,
        )

        env = os.environ.copy()
        project_root = Path(__file__).resolve().parents[2]
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(project_root)
            if not existing_pythonpath
            else f"{project_root}{os.pathsep}{existing_pythonpath}"
        )
        env.update(
            {
                "WORKFORCE_RUN_ID": run_id,
                "WORKFORCE_TASK_ID": task.task_id,
                "WORKFORCE_AGENT_ID": runtime_context.agent_id,
                "WORKFORCE_MANAGER_ID": runtime_context.manager_id or "",
                "WORKFORCE_TASK_CONTRACT_PATH": str(task_contract_path),
                "WORKFORCE_RUNTIME_DB": str(runtime_context.db_path),
                "WORKFORCE_WORKSPACE": str(runtime_context.workspace),
                "WORKFORCE_AGENT_RUN_DIR": str(task_dir),
                "WORKFORCE_MCP_COMMAND": "python3 -m workforce_runtime mcp serve",
            }
        )

        effective_timeout = self.timeout_seconds
        if task.budget.max_runtime_seconds > 0:
            effective_timeout = (
                task.budget.max_runtime_seconds
                if effective_timeout is None
                else min(effective_timeout, task.budget.max_runtime_seconds)
            )

        streamed = run_process_streaming(
            command=self.command,
            cwd=runtime_context.workspace,
            env=env,
            timeout_seconds=effective_timeout,
            runtime=runtime_context.runtime,
            file_store=file_store,
            run_id=run_id,
            task_id=task.task_id,
            agent_id=runtime_context.agent_id,
            timeout_message="worker timed out",
            run_dir=task_dir,
        )
        returncode = streamed.returncode
        stdout_path = streamed.stdout_path
        stderr_path = streamed.stderr_path
        if streamed.timed_out:
            runtime_context.runtime.record_budget_violation(
                task_id=task.task_id,
                actor_id=runtime_context.agent_id,
                reason="worker exceeded runtime budget",
                usage={"runtime_seconds": int(effective_timeout or 0)},
            )

        final_status = "failed" if streamed.timed_out else ("completed" if returncode == 0 else "failed")
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
        self._usage[run_id] = {"tokens_used": 0, "runtime_seconds": 0, "tool_calls": 0}
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
