from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def report_to_human(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    event = runtime.report_to_human(
        from_agent_id=str(arguments["from_agent_id"]),
        task_id=str(arguments["task_id"]) if arguments.get("task_id") else None,
        title=str(arguments.get("title") or ""),
        message=str(arguments["message"]),
        status=str(arguments.get("status") or ""),
        confidence=float(arguments["confidence"]) if arguments.get("confidence") is not None else None,
        next_action=str(arguments.get("next_action") or ""),
        requires_decision=bool(arguments.get("requires_decision", False)),
    )
    return {
        "ok": True,
        "event_id": event.event_id,
        "human_report_id": str(event.payload.get("human_report_id") or ""),
        "task_id": event.task_id or "",
    }
