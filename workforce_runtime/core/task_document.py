from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TaskDocumentType = Literal[
    "requirements",
    "division_of_work",
    "context",
    "decision",
    "note",
    "risk",
    "tool_request",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    doc_type: TaskDocumentType = "note"
    content: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    updated_by: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
