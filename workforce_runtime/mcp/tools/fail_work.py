from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def fail_work(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    item = runtime.fail_work_item(
        str(arguments["work_item_id"]),
        actor_id=str(arguments.get("from_agent_id") or arguments.get("actor_id") or "dispatcher"),
        error=str(arguments.get("error") or "work item failed"),
        retry=bool(arguments.get("retry", True)),
    )
    return {"ok": True, "work_item": item.model_dump(mode="json"), "work_item_id": item.work_item_id}
