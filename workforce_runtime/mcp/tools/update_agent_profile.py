from __future__ import annotations

from workforce_runtime.core import AgentExperience
from workforce_runtime.server.runtime import WorkforceRuntime


def update_agent_profile(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    actor_id = str(arguments.get("from_agent_id") or arguments.get("actor_id") or arguments["agent_id"])
    agent_id = str(arguments.get("target_agent_id") or arguments.get("agent_id") or actor_id)
    experience = _experience_from_arguments(arguments)
    profile = runtime.update_agent_personal_profile(
        actor_id=actor_id,
        agent_id=agent_id,
        summary=_optional_string(arguments.get("summary")),
        knows_about=_string_list(arguments.get("knows_about")),
        can_do=_string_list(arguments.get("can_do")),
        specialty_tags=_string_list(arguments.get("specialty_tags")),
        preferred_tools=_string_list(arguments.get("preferred_tools")),
        boundaries=_string_list(arguments.get("boundaries")),
        experience=experience,
    )
    return {"ok": True, "profile": profile.model_dump(mode="json")}


def _experience_from_arguments(arguments: dict[str, object]) -> AgentExperience | None:
    source = arguments.get("experience")
    if source is not None and not isinstance(source, dict):
        raise ValueError("experience must be an object")
    payload = dict(source or {})
    if arguments.get("task_id") and "task_id" not in payload:
        payload["task_id"] = arguments["task_id"]
    if arguments.get("task_title") and "title" not in payload:
        payload["title"] = arguments["task_title"]
    if arguments.get("task_summary") and "summary" not in payload:
        payload["summary"] = arguments["task_summary"]
    if arguments.get("outcome") and "outcome" not in payload:
        payload["outcome"] = arguments["outcome"]
    if not payload:
        return None
    return AgentExperience(
        task_id=str(payload.get("task_id") or ""),
        title=str(payload.get("title") or ""),
        summary=str(payload.get("summary") or ""),
        outcome=str(payload.get("outcome") or ""),
        skills=_string_list(payload.get("skills")) or [],
        evidence=_string_list(payload.get("evidence")) or [],
        confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return [str(item) for item in value]
