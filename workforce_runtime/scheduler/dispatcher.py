from __future__ import annotations

from workforce_runtime.core.task import TaskContract


class Dispatcher:
    def can_dispatch(self, task: TaskContract) -> bool:
        return task.assigned_to is not None and task.status in {"assigned", "in_progress"}
