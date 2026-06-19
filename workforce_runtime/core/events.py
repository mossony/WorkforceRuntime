from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
