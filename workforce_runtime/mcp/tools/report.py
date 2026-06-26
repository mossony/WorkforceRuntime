from __future__ import annotations

from uuid import uuid4

from workforce_runtime.core import ReportContract, UsageCost
from workforce_runtime.mcp.tools._validation import coerce_confidence
from workforce_runtime.server.runtime import WorkforceRuntime


def report(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    cost_input = arguments.get("cost") or {}
    if not isinstance(cost_input, dict):
        raise ValueError("cost must be an object")

    report_id = str(arguments.get("report_id") or f"report_{uuid4().hex[:12]}")
    raw_confidence = arguments.get("confidence")
    confidence = coerce_confidence(raw_confidence, default=0.5)
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
        confidence=confidence if confidence is not None else 0.5,
        cost=UsageCost(
            tokens_used=int(cost_input.get("tokens_used", 0)),
            runtime_seconds=int(cost_input.get("runtime_seconds", 0)),
            tool_calls=int(cost_input.get("tool_calls", 0)),
        ),
        next_action=str(arguments.get("next_action") or ""),
        requires_decision=bool(arguments.get("requires_decision", False)),
        alignment_check=str(arguments.get("alignment_check") or ""),
    )
    if contract.confidence != raw_confidence:
        runtime.record_event(
            event_type="mcp_tool_input_normalized",
            actor_id=contract.from_agent_id,
            task_id=contract.task_id,
            payload={
                "tool_name": "report",
                "field": "confidence",
                "input": str(raw_confidence),
                "normalized": contract.confidence,
            },
        )
    runtime.register_report(contract)
    return {
        "ok": True,
        "report_id": contract.report_id,
        "to_agent_id": contract.to_agent_id,
        "confidence": contract.confidence,
    }
