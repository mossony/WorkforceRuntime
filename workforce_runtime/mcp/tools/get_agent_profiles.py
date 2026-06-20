from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def get_agent_profiles(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    actor_id = str(arguments.get("from_agent_id") or arguments.get("actor_id") or arguments.get("agent_id") or "runtime")
    target_agent_id = arguments.get("target_agent_id")
    if target_agent_id:
        profile = runtime.get_agent_personal_profile(actor_id=actor_id, agent_id=str(target_agent_id))
        return {"ok": True, "profiles": [profile.model_dump(mode="json")]}
    profiles = runtime.list_visible_agent_personal_profiles(actor_id=actor_id)
    return {"ok": True, "profiles": [profile.model_dump(mode="json") for profile in profiles]}
