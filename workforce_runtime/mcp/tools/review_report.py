from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def review_report(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    report_id = str(arguments["report_id"])
    reviewer_id = str(arguments.get("from_agent_id") or arguments.get("reviewer_id") or arguments["agent_id"])
    decision = str(arguments["decision"])
    task = runtime.review_report(
        report_id,
        reviewer_id=reviewer_id,
        decision=decision,
        notes=str(arguments.get("notes") or ""),
    )
    return {
        "ok": True,
        "report_id": report_id,
        "task_id": task.task_id,
        "status": task.status,
        "decision": decision,
    }
