from __future__ import annotations

from workforce_runtime.core import Budget
from workforce_runtime.server.runtime import WorkforceRuntime


def hire(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    agent_input = arguments.get("agent") or {}
    if agent_input and not isinstance(agent_input, dict):
        raise ValueError("agent must be an object")

    source = {**agent_input, **arguments} if isinstance(agent_input, dict) else arguments
    budget_input = source.get("budget") or {}
    if not isinstance(budget_input, dict):
        raise ValueError("budget must be an object")
    agent = runtime.hire_agent(
        requested_by=str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"]),
        agent_id=str(source["new_agent_id"] if source.get("new_agent_id") else source["id"]),
        name=str(source["name"]),
        role=str(source["role"]),
        department=str(source["department"]),
        manager_id=str(source["manager_id"]),
        worker_type=str(source["worker_type"]),
        model=str(source.get("model") or ""),
        responsibilities=[str(item) for item in source.get("responsibilities") or []],
        permissions=[str(item) for item in source.get("permissions") or []],
        budget=Budget(
            max_tokens=int(budget_input.get("max_tokens", 0)),
            max_runtime_seconds=int(budget_input.get("max_runtime_seconds", 0)),
            max_tool_calls=int(budget_input.get("max_tool_calls", 0)),
        ),
        system_prompt=str(source.get("system_prompt") or ""),
    )
    return {"ok": True, "agent": agent.model_dump(mode="json")}
