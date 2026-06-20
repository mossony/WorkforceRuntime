from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def decide_tool_request(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    return runtime.decide_tool_request(
        actor_id=str(arguments.get("actor_id") or arguments.get("agent_id") or arguments.get("from_agent_id")),
        request_id=str(arguments["request_id"]),
        decision=str(arguments["decision"]),
        notes=str(arguments.get("notes") or ""),
        approval_level=str(arguments.get("approval_level") or ""),
    )
