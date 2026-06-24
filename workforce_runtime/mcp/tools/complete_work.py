from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def complete_work(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    result = arguments.get("result") or {}
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    item = runtime.complete_work_item(
        str(arguments["work_item_id"]),
        actor_id=str(arguments.get("from_agent_id") or arguments.get("actor_id") or "dispatcher"),
        result=result,
    )
    return {"ok": True, "work_item": item.model_dump(mode="json"), "work_item_id": item.work_item_id}
