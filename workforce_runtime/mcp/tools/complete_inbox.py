from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def complete_inbox(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    result = arguments.get("result") or {}
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    item = runtime.complete_agent_inbox_item(
        str(arguments["inbox_item_id"]),
        actor_id=str(arguments.get("from_agent_id") or arguments.get("actor_id") or "runtime"),
        result=result,
    )
    return {"ok": True, "item": item.model_dump(mode="json"), "inbox_item_id": item.inbox_item_id}
