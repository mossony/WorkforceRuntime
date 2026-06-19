from __future__ import annotations

from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.core.permissions import Capability


class PermissionManager:
    def has_permission(self, agent: AgentProfile, capability: Capability) -> bool:
        return agent.has_permission(capability)

    def require_permission(self, agent: AgentProfile, capability: Capability) -> None:
        if not self.has_permission(agent, capability):
            raise PermissionError(f"agent {agent.id} lacks permission: {capability}")
