from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def escalate_clarification(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    clarification = runtime.escalate_clarification(
        clarification_id=str(arguments["clarification_id"]),
        from_agent_id=str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"]),
        note=str(arguments.get("note") or ""),
    )
    return {
        "ok": True,
        "clarification_id": clarification.clarification_id,
        "status": clarification.status,
        "current_holder_id": clarification.current_holder_id,
    }
