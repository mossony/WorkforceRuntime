from __future__ import annotations

from typing import Any

from workforce_runtime.config import model_capabilities
from workforce_runtime.server.runtime import WorkforceRuntime


def get_org_context(runtime: WorkforceRuntime, _arguments: dict[str, object]) -> dict[str, object]:
    agents = runtime.store.list_agents()
    return {"ok": True, "agents": [_agent_context(agent) for agent in agents]}


def _agent_context(agent: Any) -> dict[str, object]:
    payload = agent.model_dump(mode="json")
    payload["model_capabilities"] = model_capabilities(str(payload.get("model") or "")) or {}
    return payload
