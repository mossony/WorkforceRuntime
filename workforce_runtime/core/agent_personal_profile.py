from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = ""
    title: str = ""
    summary: str = ""
    outcome: str = ""
    skills: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)


class AgentPersonalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    summary: str = ""
    knows_about: list[str] = Field(default_factory=list)
    can_do: list[str] = Field(default_factory=list)
    specialty_tags: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    experiences: list[AgentExperience] = Field(default_factory=list)
    updated_by: str = ""
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
