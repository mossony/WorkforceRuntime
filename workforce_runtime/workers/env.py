from __future__ import annotations

import os
from pathlib import Path

from workforce_runtime.core import TaskContract
from workforce_runtime.workers.base import RuntimeContext


def worker_process_env(
    runtime_context: RuntimeContext,
    *,
    run_id: str,
    task: TaskContract,
    task_contract_path: Path,
    run_dir: Path,
) -> dict[str, str]:
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
            "WORKFORCE_AGENT_RUN_DIR": str(run_dir),
            "WORKFORCE_MCP_COMMAND": "python3 -m workforce_runtime mcp serve",
        }
    )
    return env
