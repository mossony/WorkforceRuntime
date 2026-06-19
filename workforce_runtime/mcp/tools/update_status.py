from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def update_status(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    task = runtime.update_task_status(
        str(arguments["task_id"]),
        status=str(arguments["status"]),  # type: ignore[arg-type]
        actor_id=str(arguments["agent_id"]),
    )
    return {"ok": True, "task_id": task.task_id, "status": task.status}
