from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from workforce_runtime.core import TaskContract
from workforce_runtime.server.runtime import WorkforceRuntime


@dataclass(frozen=True)
class RuntimeContext:
    runtime: WorkforceRuntime
    db_path: Path
    workspace: Path
    agent_id: str
    manager_id: str | None = None


@dataclass(frozen=True)
class WorkerRun:
    run_id: str
    task_id: str
    returncode: int | None
    stdout_path: Path
    stderr_path: Path
    task_contract_path: Path


class WorkerAdapter(Protocol):
    def declare_capabilities(self) -> list[str]:
        ...

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        ...

    def collect_artifacts(self, run_id: str) -> list[Path]:
        ...

    def stop_task(self, run_id: str) -> None:
        ...

    def get_usage(self, run_id: str) -> dict[str, int]:
        ...
