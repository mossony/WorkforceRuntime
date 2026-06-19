from __future__ import annotations

from uuid import uuid4

from workforce_runtime.core import ReportContract, UsageCost
from workforce_runtime.server.runtime import WorkforceRuntime


def report(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    cost_input = arguments.get("cost") or {}
    if not isinstance(cost_input, dict):
        raise ValueError("cost must be an object")

    report_id = str(arguments.get("report_id") or f"report_{uuid4().hex[:12]}")
    contract = ReportContract(
        report_id=report_id,
        from_agent_id=str(arguments["from_agent_id"]),
        to_agent_id=str(arguments.get("to_agent_id") or runtime.get_report_recipient(str(arguments["from_agent_id"]))),
        task_id=str(arguments["task_id"]),
        summary=str(arguments["summary"]),
        status=str(arguments["status"]),
        work_done=list(arguments.get("work_done") or []),
        evidence=list(arguments.get("evidence") or []),
        risks=list(arguments.get("risks") or []),
        blockers=list(arguments.get("blockers") or []),
        confidence=float(arguments["confidence"]),
        cost=UsageCost(
            tokens_used=int(cost_input.get("tokens_used", 0)),
            runtime_seconds=int(cost_input.get("runtime_seconds", 0)),
            tool_calls=int(cost_input.get("tool_calls", 0)),
        ),
        next_action=str(arguments.get("next_action") or ""),
        requires_decision=bool(arguments.get("requires_decision", False)),
        alignment_check=str(arguments.get("alignment_check") or ""),
    )
    runtime.register_report(contract)
    return {"ok": True, "report_id": contract.report_id, "to_agent_id": contract.to_agent_id}
