from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def update_system_prompt(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    agent = runtime.update_system_prompt(
        actor_id=str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"]),
        target_agent_id=str(arguments.get("target_agent_id") or arguments.get("to_agent_id") or arguments["id"]),
        system_prompt=str(arguments.get("system_prompt") or arguments["message"]),
    )
    return {"ok": True, "agent_id": agent.id, "system_prompt": agent.system_prompt}
