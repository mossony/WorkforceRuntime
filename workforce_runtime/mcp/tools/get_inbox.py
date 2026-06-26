from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def get_inbox(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    status = arguments.get("status")
    items = runtime.list_agent_inbox_items(
        agent_id=str(arguments["agent_id"]) if arguments.get("agent_id") else None,
        status=str(status) if status else None,  # type: ignore[arg-type]
        actor_id=str(arguments.get("from_agent_id") or arguments.get("actor_id") or arguments.get("agent_id") or "runtime"),
    )
    return {"ok": True, "items": [item.model_dump(mode="json") for item in items], "count": len(items)}
