from __future__ import annotations

from pathlib import Path


class FileStore:
    def __init__(self, base_dir: str | Path = ".") -> None:
        self.base_dir = Path(base_dir)
        self.artifacts_dir = self.base_dir / "artifacts"

    def task_artifact_dir(self, task_id: str) -> Path:
        path = self.artifacts_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def agent_task_run_dir(self, *, agent_id: str, task_id: str, run_id: str) -> Path:
        path = self.base_dir / "agents" / _safe_path_part(agent_id) / "tasks" / _safe_path_part(task_id) / "runs" / _safe_path_part(run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_artifact_file(self, task_id: str, filename: str, content: str | bytes) -> Path:
        path = self.task_artifact_dir(task_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def save_worker_stdout(
        self,
        task_id: str,
        content: str,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> Path:
        if agent_id and run_id:
            path = self.agent_task_run_dir(agent_id=agent_id, task_id=task_id, run_id=run_id) / "stdout.log"
            path.write_text(content)
            return path
        return self.save_artifact_file(task_id, "stdout.log", content)

    def save_worker_stderr(
        self,
        task_id: str,
        content: str,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> Path:
        if agent_id and run_id:
            path = self.agent_task_run_dir(agent_id=agent_id, task_id=task_id, run_id=run_id) / "stderr.log"
            path.write_text(content)
            return path
        return self.save_artifact_file(task_id, "stderr.log", content)

    def save_git_diff(self, task_id: str, diff: str) -> Path:
        return self.save_artifact_file(task_id, "diff.patch", diff)

    def save_test_log(self, task_id: str, log: str, filename: str = "test.log") -> Path:
        return self.save_artifact_file(task_id, filename, log)

    @staticmethod
    def workspace_from_run_file(path: Path) -> Path:
        parts = path.parts
        if "agents" not in parts:
            return path.parent
        index = parts.index("agents")
        if index == 0:
            return Path(".")
        return Path(*parts[:index])


def _safe_path_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value)
    return safe.strip("._") or "unknown"
