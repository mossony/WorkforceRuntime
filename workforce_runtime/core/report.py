from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core.budget import UsageCost


class ReportContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(min_length=1)
    from_agent_id: str = Field(min_length=1)
    to_agent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    status: str = Field(min_length=1)
    work_done: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    cost: UsageCost = Field(default_factory=UsageCost)
    next_action: str = ""
    requires_decision: bool = False
    alignment_check: str = ""
