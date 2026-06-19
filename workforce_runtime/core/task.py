from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core.budget import Budget


TaskStatus = Literal[
    "created",
    "assigned",
    "in_progress",
    "blocked",
    "completed",
    "failed",
    "cancelled",
]


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    assigned_to: str | None = None
    assigned_by: str | None = None
    parent_task_id: str | None = None
    root_goal_id: str | None = None
    context_refs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    risk_level: str = "medium"
    required_artifacts: list[str] = Field(default_factory=list)
    status: TaskStatus = "created"
