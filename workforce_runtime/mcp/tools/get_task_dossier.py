from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def get_task_dossier(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    return runtime.get_task_dossier(
        actor_id=str(arguments.get("actor_id") or arguments.get("agent_id") or arguments.get("from_agent_id") or "runtime"),
        task_id=str(arguments["task_id"]),
        include_events=bool(arguments.get("include_events", True)),
        event_limit=int(arguments.get("event_limit") or 20),
    )
