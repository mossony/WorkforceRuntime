from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def request_tool(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    return runtime.request_tool(
        actor_id=str(arguments.get("actor_id") or arguments.get("agent_id") or arguments.get("from_agent_id")),
        task_id=str(arguments["task_id"]) if arguments.get("task_id") else None,
        tool_name=str(arguments["tool_name"]),
        problem=str(arguments["problem"]),
        proposed_capability=str(arguments["proposed_capability"]),
        frequency=str(arguments.get("frequency") or ""),
        current_workaround=str(arguments.get("current_workaround") or ""),
        requested_approval_level=str(arguments.get("requested_approval_level") or "human_ceo"),
    )
