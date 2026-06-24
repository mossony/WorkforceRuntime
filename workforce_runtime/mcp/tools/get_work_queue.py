from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def get_work_queue(runtime: WorkforceRuntime, _arguments: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "queue": runtime.work_queue_snapshot()}
