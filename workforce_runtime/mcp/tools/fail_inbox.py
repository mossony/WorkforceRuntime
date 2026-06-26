from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def fail_inbox(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    item = runtime.fail_agent_inbox_item(
        str(arguments["inbox_item_id"]),
        actor_id=str(arguments.get("from_agent_id") or arguments.get("actor_id") or "runtime"),
        error=str(arguments.get("error") or "inbox item failed"),
        retry=bool(arguments.get("retry", True)),
    )
    return {"ok": True, "item": item.model_dump(mode="json"), "inbox_item_id": item.inbox_item_id}
