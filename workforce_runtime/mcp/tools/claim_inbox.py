from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def claim_inbox(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    actor_id = str(arguments.get("from_agent_id") or arguments.get("actor_id") or arguments["agent_id"])
    items = runtime.claim_agent_inbox_items(
        agent_id=str(arguments["agent_id"]),
        lease_owner=str(arguments.get("lease_owner") or actor_id),
        limit=int(arguments.get("limit") or 1),
        actor_id=actor_id,
    )
    return {"ok": True, "items": [item.model_dump(mode="json") for item in items], "count": len(items)}
