from __future__ import annotations

from workforce_runtime.config import model_capabilities
from workforce_runtime.server.runtime import WorkforceRuntime


def get_task_context(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    task = runtime.require_task(str(arguments["task_id"]))
    reports = runtime.store.list_reports_by_task(task.task_id)
    artifacts = runtime.store.list_artifacts_by_task(task.task_id)
    documents = runtime.store.list_task_documents_by_task(task.task_id)
    actor_id = str(arguments.get("actor_id") or arguments.get("agent_id") or "")
    actor = runtime.get_agent(actor_id) if actor_id else None
    return {
        "ok": True,
        "task": task.model_dump(mode="json"),
        "reports": [report.model_dump(mode="json") for report in reports],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "documents": [document.model_dump(mode="json") for document in documents],
        "actor_model_capabilities": model_capabilities(actor.model if actor is not None else "") or {},
    }
