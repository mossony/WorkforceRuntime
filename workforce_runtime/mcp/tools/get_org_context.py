from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def get_org_context(runtime: WorkforceRuntime, _arguments: dict[str, object]) -> dict[str, object]:
    agents = runtime.store.list_agents()
    return {"ok": True, "agents": [agent.model_dump(mode="json") for agent in agents]}
