from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def answer_clarification(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    clarification = runtime.answer_clarification(
        clarification_id=str(arguments["clarification_id"]),
        from_agent_id=str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"]),
        answer=str(arguments["answer"]),
    )
    return {
        "ok": True,
        "clarification_id": clarification.clarification_id,
        "status": clarification.status,
        "answered_by": clarification.answered_by,
        "origin_task_id": clarification.origin_task_id or "",
    }
