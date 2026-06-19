from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def check_progress(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    result = runtime.check_progress(
        manager_id=str(arguments.get("from_agent_id") or arguments.get("manager_id") or arguments["agent_id"]),
        target_agent_id=str(arguments.get("target_agent_id") or arguments.get("worker_id") or arguments["to_agent_id"]),
        message=str(arguments.get("message") or ""),
        task_id=str(arguments["task_id"]) if arguments.get("task_id") else None,
    )
    return {"ok": True, **result}
