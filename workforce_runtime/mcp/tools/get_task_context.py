from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def get_task_context(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    task = runtime.require_task(str(arguments["task_id"]))
    reports = runtime.store.list_reports_by_task(task.task_id)
    artifacts = runtime.store.list_artifacts_by_task(task.task_id)
    return {
        "ok": True,
        "task": task.model_dump(mode="json"),
        "reports": [report.model_dump(mode="json") for report in reports],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
    }
