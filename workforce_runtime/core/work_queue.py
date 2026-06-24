from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core.events import utc_now


WorkItemKind = Literal["llm_request", "tool_call", "worker_run"]
WorkItemStatus = Literal["queued", "leased", "completed", "failed", "cancelled"]

TERMINAL_WORK_ITEM_STATUSES: set[WorkItemStatus] = {"completed", "failed", "cancelled"}


class WorkQueuePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_active_agents: int = Field(default=20, ge=1)
    lease_seconds: int = Field(default=300, ge=1)
    per_kind_limits: dict[str, int] = Field(default_factory=lambda: {"llm_request": 10, "tool_call": 20, "worker_run": 10})
    per_model_limits: dict[str, int] = Field(default_factory=dict)
    per_tool_limits: dict[str, int] = Field(default_factory=dict)
    allow_same_agent_parallel: bool = False


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_item_id: str = Field(min_length=1)
    kind: WorkItemKind
    agent_id: str = Field(min_length=1)
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    model: str = ""
    tool_name: str = ""
    idempotency_key: str | None = None
    status: WorkItemStatus = "queued"
    lease_owner: str = ""
    leased_at: datetime | None = None
    lease_until: datetime | None = None
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""

    def is_active(self, now: datetime | None = None) -> bool:
        if self.status != "leased" or self.lease_until is None:
            return False
        return self.lease_until > (now or utc_now())

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_WORK_ITEM_STATUSES
