from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core.budget import Budget
from workforce_runtime.core.permissions import Capability


AgentStatus = Literal["idle", "busy", "blocked", "suspended", "terminated"]


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    department: str = Field(min_length=1)
    manager_id: str | None = None
    worker_type: str = Field(min_length=1)
    model: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    permissions: list[Capability] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    status: AgentStatus = "idle"
    current_task_ids: list[str] = Field(default_factory=list)
    performance_summary: str = ""
    system_prompt: str = ""

    def has_permission(self, capability: Capability) -> bool:
        return capability in self.permissions
