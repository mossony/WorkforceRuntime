from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def enqueue_work(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    payload = arguments.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    item = runtime.enqueue_work_item(
        actor_id=str(arguments.get("from_agent_id") or arguments.get("actor_id") or "unknown"),
        agent_id=str(arguments["agent_id"]),
        kind=str(arguments["kind"]),  # type: ignore[arg-type]
        task_id=str(arguments["task_id"]) if arguments.get("task_id") else None,
        payload=payload,
        priority=int(arguments.get("priority") or 0),
        model=str(arguments.get("model") or ""),
        tool_name=str(arguments.get("tool_name") or ""),
        idempotency_key=str(arguments["idempotency_key"]) if arguments.get("idempotency_key") else None,
        max_attempts=int(arguments.get("max_attempts") or 3),
    )
    return {"ok": True, "work_item": item.model_dump(mode="json"), "work_item_id": item.work_item_id}
