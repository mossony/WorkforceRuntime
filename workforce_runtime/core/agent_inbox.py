from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core.events import utc_now


AgentInboxItemKind = Literal[
    "assignment", "message", "report_review", "human_steer", "system_notice", "clarification"
]
AgentInboxItemStatus = Literal["queued", "leased", "completed", "failed", "cancelled", "interrupted"]
AgentInboxInterruptMode = Literal["none", "soft", "steer", "hard"]

TERMINAL_AGENT_INBOX_STATUSES: set[AgentInboxItemStatus] = {"completed", "failed", "cancelled", "interrupted"}


class AgentInboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbox_item_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    kind: AgentInboxItemKind
    task_id: str | None = None
    from_agent_id: str = "runtime"
    thread_id: str = ""
    priority: int = 0
    interrupt_mode: AgentInboxInterruptMode = "none"
    status: AgentInboxItemStatus = "queued"
    payload: dict[str, Any] = Field(default_factory=dict)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    leased_at: datetime | None = None
    lease_owner: str = ""
    completed_at: datetime | None = None
    error: str = ""

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_AGENT_INBOX_STATUSES


class ClaimedAgentInboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: AgentInboxItem
    delivery_tag: int
