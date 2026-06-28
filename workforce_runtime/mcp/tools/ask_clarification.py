from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def ask_clarification(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    clarification = runtime.raise_clarification(
        from_agent_id=str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"]),
        question=str(arguments["question"]),
        task_id=str(arguments["task_id"]) if arguments.get("task_id") else None,
        thread_id=str(arguments.get("thread_id") or ""),
    )
    return {
        "ok": True,
        "clarification_id": clarification.clarification_id,
        "status": clarification.status,
        "current_holder_id": clarification.current_holder_id,
    }
