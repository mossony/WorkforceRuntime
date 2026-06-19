from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.core.permissions import REQUEST_BUDGET


def request_budget(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    runtime.require_permission(
        str(arguments["agent_id"]),
        REQUEST_BUDGET,
        task_id=str(arguments.get("task_id")) if arguments.get("task_id") else None,
    )
    event = runtime.record_event(
        event_type="budget_requested",
        actor_id=str(arguments["agent_id"]),
        task_id=str(arguments.get("task_id")) if arguments.get("task_id") else None,
        payload=dict(arguments),
    )
    return {"ok": True, "event_id": event.event_id, "status": "pending"}
