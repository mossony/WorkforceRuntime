from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SkillStatus = Literal["draft", "approved", "published", "archived"]
SkillAssignmentTargetType = Literal["global", "agent", "role", "department", "worker_type"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1)
    content: str = ""
    executable: bool = False


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = "1"
    status: SkillStatus = "draft"
    provider_targets: list[str] = Field(default_factory=lambda: ["codex", "claude_code"])
    files: list[SkillFile] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    checksum: str = ""
    created_by: str = Field(default="human", min_length=1)
    updated_by: str = Field(default="human", min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _has_skill_md(self) -> SkillDefinition:
        if not any(file.relative_path == "SKILL.md" for file in self.files):
            raise ValueError("skill definition must include SKILL.md")
        return self


class SkillAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    target_type: SkillAssignmentTargetType
    target_id: str = Field(default="*", min_length=1)
    enabled: bool = True
    materialize_on_start: bool = True
    created_by: str = Field(default="human", min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillMaterialization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialization_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    worker_type: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    target_dir: str = Field(min_length=1)
    task_id: str | None = None
    run_id: str = ""
    checksum: str = ""
    file_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


def skill_checksum(files: list[SkillFile]) -> str:
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda item: item.relative_path):
        digest.update(file.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.content.encode("utf-8"))
        digest.update(b"\0")
        digest.update(b"1" if file.executable else b"0")
        digest.update(b"\0")
    return digest.hexdigest()
