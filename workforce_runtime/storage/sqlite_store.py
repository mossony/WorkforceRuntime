from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.core.artifact import Artifact
from workforce_runtime.core.events import Event
from workforce_runtime.core.organization import Company
from workforce_runtime.core.report import ReportContract
from workforce_runtime.core.task import TaskContract


@dataclass(frozen=True)
class SequencedEvent:
    sequence: int
    event: Event


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                task_id TEXT,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def save_company(self, company: Company) -> None:
        self._conn.execute(
            """
            INSERT INTO metadata (key, payload)
            VALUES ('company', ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            (company.model_dump_json(),),
        )
        self._conn.commit()

    def get_company(self) -> Company | None:
        row = self._conn.execute("SELECT payload FROM metadata WHERE key = 'company'").fetchone()
        if row is None:
            return None
        return Company.model_validate_json(row["payload"])

    def save_agent(self, agent: AgentProfile) -> None:
        self._conn.execute(
            """
            INSERT INTO agents (id, payload)
            VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
            """,
            (agent.id, agent.model_dump_json()),
        )
        self._conn.commit()

    def get_agent(self, agent_id: str) -> AgentProfile | None:
        row = self._conn.execute("SELECT payload FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            return None
        return AgentProfile.model_validate_json(row["payload"])

    def list_agents(self) -> list[AgentProfile]:
        rows = self._conn.execute("SELECT payload FROM agents ORDER BY id").fetchall()
        return [AgentProfile.model_validate_json(row["payload"]) for row in rows]

    def save_task(self, task: TaskContract) -> None:
        self._conn.execute(
            """
            INSERT INTO tasks (task_id, payload)
            VALUES (?, ?)
            ON CONFLICT(task_id) DO UPDATE SET payload = excluded.payload
            """,
            (task.task_id, task.model_dump_json()),
        )
        self._conn.commit()

    def get_task(self, task_id: str) -> TaskContract | None:
        row = self._conn.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return TaskContract.model_validate_json(row["payload"])

    def list_tasks(self) -> list[TaskContract]:
        rows = self._conn.execute("SELECT payload FROM tasks ORDER BY task_id").fetchall()
        return [TaskContract.model_validate_json(row["payload"]) for row in rows]

    def save_report(self, report: ReportContract) -> None:
        self._conn.execute(
            """
            INSERT INTO reports (report_id, task_id, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
                task_id = excluded.task_id,
                payload = excluded.payload
            """,
            (report.report_id, report.task_id, report.model_dump_json()),
        )
        self._conn.commit()

    def list_reports_by_task(self, task_id: str) -> list[ReportContract]:
        rows = self._conn.execute(
            "SELECT payload FROM reports WHERE task_id = ? ORDER BY report_id",
            (task_id,),
        ).fetchall()
        return [ReportContract.model_validate_json(row["payload"]) for row in rows]

    def get_report(self, report_id: str) -> ReportContract | None:
        row = self._conn.execute("SELECT payload FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        return ReportContract.model_validate_json(row["payload"])

    def list_reports(self) -> list[ReportContract]:
        rows = self._conn.execute("SELECT payload FROM reports ORDER BY report_id").fetchall()
        return [ReportContract.model_validate_json(row["payload"]) for row in rows]

    def save_event(self, event: Event) -> None:
        self._conn.execute(
            """
            INSERT INTO events (event_id, timestamp, task_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (event.event_id, event.timestamp.isoformat(), event.task_id, event.model_dump_json()),
        )
        self._conn.commit()

    def list_events(self) -> list[Event]:
        rows = self._conn.execute("SELECT payload FROM events ORDER BY sequence").fetchall()
        return [Event.model_validate_json(row["payload"]) for row in rows]

    def list_events_after(self, sequence: int = 0, *, limit: int = 500) -> list[SequencedEvent]:
        rows = self._conn.execute(
            """
            SELECT sequence, payload
            FROM events
            WHERE sequence > ?
            ORDER BY sequence
            LIMIT ?
            """,
            (sequence, limit),
        ).fetchall()
        return [
            SequencedEvent(sequence=int(row["sequence"]), event=Event.model_validate_json(row["payload"]))
            for row in rows
        ]

    def latest_event_sequence(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM events").fetchone()
        return int(row["sequence"])

    def save_artifact(self, artifact: Artifact) -> None:
        self._conn.execute(
            """
            INSERT INTO artifacts (artifact_id, task_id, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                task_id = excluded.task_id,
                payload = excluded.payload
            """,
            (artifact.artifact_id, artifact.task_id, artifact.model_dump_json()),
        )
        self._conn.commit()

    def list_artifacts_by_task(self, task_id: str) -> list[Artifact]:
        rows = self._conn.execute(
            "SELECT payload FROM artifacts WHERE task_id = ? ORDER BY artifact_id",
            (task_id,),
        ).fetchall()
        return [Artifact.model_validate_json(row["payload"]) for row in rows]

    def list_artifacts(self) -> list[Artifact]:
        rows = self._conn.execute("SELECT payload FROM artifacts ORDER BY artifact_id").fetchall()
        return [Artifact.model_validate_json(row["payload"]) for row in rows]
