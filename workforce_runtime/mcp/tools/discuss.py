from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def discuss(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    event = runtime.send_discussion_message(
        from_agent_id=str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"]),
        to_agent_id=str(arguments.get("to_agent_id") or arguments.get("recipient_id") or arguments["worker_id"]),
        message=str(arguments["message"]),
        task_id=str(arguments["task_id"]) if arguments.get("task_id") else None,
        thread_id=str(arguments.get("thread_id") or arguments.get("id")) if arguments.get("thread_id") or arguments.get("id") else None,
    )
    return {"ok": True, "event_id": event.event_id, "to_agent_id": event.payload["to_agent_id"]}
