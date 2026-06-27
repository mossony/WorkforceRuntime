from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import Any

from workforce_runtime.core.skill import SkillDefinition, skill_checksum


def skill_root_for_worker_type(
    *,
    workspace: Path,
    worker_type: str,
    config: dict[str, Any] | None = None,
) -> Path | None:
    skills_config = config or {}
    roots = skills_config.get("roots") if isinstance(skills_config.get("roots"), dict) else {}
    normalized = worker_type.strip().lower()
    if normalized == "codex":
        return workspace / str(roots.get("codex") or ".agents/skills")
    if normalized in {"claude_code", "claude_code_interactive"}:
        return workspace / str(roots.get("claude_code") or ".claude/skills")
    return None


def materialize_skill_definition(
    *,
    skill: SkillDefinition,
    workspace: Path,
    worker_type: str,
    config: dict[str, Any] | None = None,
) -> tuple[Path, list[Path]]:
    root = skill_root_for_worker_type(workspace=workspace, worker_type=worker_type, config=config)
    if root is None:
        raise ValueError(f"worker type {worker_type!r} does not support native skills")
    skill_dir = root / safe_skill_directory_name(skill.name)
    _assert_under_workspace(skill_dir, workspace)
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for file in skill.files:
        relative_path = _safe_relative_path(file.relative_path)
        target = skill_dir / relative_path
        _assert_under_workspace(target, workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content)
        if file.executable:
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
        written.append(target)
    return skill_dir, written


def safe_skill_directory_name(name: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "-" for char in name.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe.lower() or "skill"


def effective_skill_checksum(skill: SkillDefinition) -> str:
    return skill.checksum or skill_checksum(skill.files)


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"skill file path must be relative: {value}")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe skill file path: {value}")
    return path


def _assert_under_workspace(path: Path, workspace: Path) -> None:
    resolved_workspace = workspace.resolve()
    resolved_path = path.resolve() if path.exists() else path.parent.resolve() / path.name
    try:
        resolved_path.relative_to(resolved_workspace)
    except ValueError as exc:
        raise ValueError(f"skill materialization path escapes workspace: {path}") from exc
