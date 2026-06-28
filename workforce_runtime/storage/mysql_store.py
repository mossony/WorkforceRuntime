from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymysql
from pymysql.err import OperationalError
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from workforce_runtime.config.runtime_config import load_runtime_config
from workforce_runtime.core.agent_inbox import AgentInboxItem, AgentInboxItemStatus
from workforce_runtime.core.agent_personal_profile import AgentPersonalProfile
from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.core.artifact import Artifact
from workforce_runtime.core.clarification import Clarification
from workforce_runtime.core.events import Event
from workforce_runtime.core.organization import Company
from workforce_runtime.core.report import ReportContract
from workforce_runtime.core.skill import SkillAssignment, SkillDefinition, SkillMaterialization
from workforce_runtime.core.task import TaskContract
from workforce_runtime.core.task_document import TaskDocument
from workforce_runtime.core.task_trace import TaskTraceExport
from workforce_runtime.core.work_queue import WorkItem, WorkItemStatus
from workforce_runtime.storage.base import RuntimeStore, SequencedEvent


class MySQLStore(RuntimeStore):
    def __init__(self, database: str | Path = "workforce_runtime", *, config: dict[str, Any] | None = None) -> None:
        settings = dict(config or load_runtime_config().get("mysql", {}))
        configured_database = str(settings.get("database") or "workforce_runtime")
        self.database = _database_name(database) or configured_database
        self.host = str(settings.get("host") or "127.0.0.1")
        self.port = int(settings.get("port") or 3306)
        self.username = str(settings.get("username") or "workforce")
        self.password = str(settings.get("password") or "workforce")
        self.charset = str(settings.get("charset") or "utf8mb4")
        self.connect_timeout = int(settings.get("connect_timeout") or 10)
        try:
            self._ensure_database()
        except OperationalError as exc:
            if exc.args and exc.args[0] == 1044:
                if self.database != configured_database:
                    self.database = configured_database
            else:
                raise
        self._conn = self._connect(database=self.database)
        self._initialize_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MySQLStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _connect(self, *, database: str | None = None) -> Connection:
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            database=database,
            charset=self.charset,
            connect_timeout=self.connect_timeout,
            autocommit=False,
            cursorclass=DictCursor,
        )

    def _ensure_database(self) -> None:
        conn = self._connect(database=None)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id VARCHAR(255) PRIMARY KEY,
                    payload LONGTEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_personal_profiles (
                    agent_id VARCHAR(255) PRIMARY KEY,
                    payload LONGTEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_inbox_items (
                    inbox_item_id VARCHAR(255) PRIMARY KEY,
                    agent_id VARCHAR(255) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    kind VARCHAR(64) NOT NULL,
                    task_id VARCHAR(255),
                    priority INT NOT NULL,
                    created_at VARCHAR(64) NOT NULL,
                    updated_at VARCHAR(64) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_agent_inbox_items_agent_status (agent_id, status, priority, created_at),
                    INDEX idx_agent_inbox_items_task (task_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id VARCHAR(255) PRIMARY KEY,
                    payload LONGTEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_definitions (
                    skill_id VARCHAR(255) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_skill_definitions_name (name),
                    INDEX idx_skill_definitions_status (status)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_assignments (
                    assignment_id VARCHAR(255) PRIMARY KEY,
                    skill_id VARCHAR(255) NOT NULL,
                    target_type VARCHAR(64) NOT NULL,
                    target_id VARCHAR(255) NOT NULL,
                    enabled TINYINT(1) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_skill_assignments_target (target_type, target_id, enabled),
                    INDEX idx_skill_assignments_skill (skill_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_materializations (
                    materialization_id VARCHAR(255) PRIMARY KEY,
                    skill_id VARCHAR(255) NOT NULL,
                    agent_id VARCHAR(255) NOT NULL,
                    task_id VARCHAR(255),
                    run_id VARCHAR(255) NOT NULL,
                    created_at VARCHAR(64) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_skill_materializations_agent (agent_id, created_at),
                    INDEX idx_skill_materializations_skill (skill_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    report_id VARCHAR(255) PRIMARY KEY,
                    task_id VARCHAR(255) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_reports_task (task_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS clarifications (
                    clarification_id VARCHAR(255) PRIMARY KEY,
                    task_id VARCHAR(255),
                    status VARCHAR(64) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_clarifications_status (status)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence BIGINT PRIMARY KEY AUTO_INCREMENT,
                    event_id VARCHAR(255) NOT NULL UNIQUE,
                    timestamp VARCHAR(64) NOT NULL,
                    task_id VARCHAR(255),
                    payload LONGTEXT NOT NULL,
                    INDEX idx_events_task_sequence (task_id, sequence)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id VARCHAR(255) PRIMARY KEY,
                    task_id VARCHAR(255) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_artifacts_task (task_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_documents (
                    doc_id VARCHAR(255) PRIMARY KEY,
                    task_id VARCHAR(255) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_task_documents_task (task_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_trace_exports (
                    trace_id VARCHAR(255) PRIMARY KEY,
                    task_id VARCHAR(255) NOT NULL,
                    exported_at VARCHAR(64) NOT NULL,
                    path TEXT NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_task_trace_exports_task (task_id, exported_at)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS work_items (
                    work_item_id VARCHAR(255) PRIMARY KEY,
                    status VARCHAR(64) NOT NULL,
                    kind VARCHAR(64) NOT NULL,
                    agent_id VARCHAR(255) NOT NULL,
                    task_id VARCHAR(255),
                    priority INT NOT NULL,
                    model VARCHAR(512) NOT NULL,
                    tool_name VARCHAR(255) NOT NULL,
                    idempotency_key VARCHAR(512),
                    lease_owner VARCHAR(255) NOT NULL,
                    lease_until VARCHAR(64),
                    created_at VARCHAR(64) NOT NULL,
                    updated_at VARCHAR(64) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    INDEX idx_work_items_status_priority (status, priority, created_at),
                    INDEX idx_work_items_agent (agent_id),
                    INDEX idx_work_items_task (task_id),
                    INDEX idx_work_items_idempotency (idempotency_key)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    `key` VARCHAR(255) PRIMARY KEY,
                    payload LONGTEXT NOT NULL
                )
                """
            )
        self._conn.commit()

    def save_company(self, company: Company) -> None:
        self._execute_upsert("metadata", ["`key`", "payload"], ("company", company.model_dump_json()))

    def get_company(self) -> Company | None:
        row = self._fetchone("SELECT payload FROM metadata WHERE `key` = %s", ("company",))
        return None if row is None else Company.model_validate_json(row["payload"])

    def save_agent(self, agent: AgentProfile) -> None:
        self._execute_upsert("agents", ["id", "payload"], (agent.id, agent.model_dump_json()))

    def get_agent(self, agent_id: str) -> AgentProfile | None:
        row = self._fetchone("SELECT payload FROM agents WHERE id = %s", (agent_id,))
        return None if row is None else AgentProfile.model_validate_json(row["payload"])

    def list_agents(self) -> list[AgentProfile]:
        rows = self._fetchall("SELECT payload FROM agents ORDER BY id")
        return [AgentProfile.model_validate_json(row["payload"]) for row in rows]

    def save_agent_inbox_item(self, item: AgentInboxItem) -> None:
        self._execute_upsert(
            "agent_inbox_items",
            ["inbox_item_id", "agent_id", "status", "kind", "task_id", "priority", "created_at", "updated_at", "payload"],
            (
                item.inbox_item_id,
                item.agent_id,
                item.status,
                item.kind,
                item.task_id,
                item.priority,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.model_dump_json(),
            ),
        )

    def get_agent_inbox_item(self, inbox_item_id: str) -> AgentInboxItem | None:
        row = self._fetchone("SELECT payload FROM agent_inbox_items WHERE inbox_item_id = %s", (inbox_item_id,))
        return None if row is None else AgentInboxItem.model_validate_json(row["payload"])

    def list_agent_inbox_items(
        self,
        *,
        agent_id: str | None = None,
        status: AgentInboxItemStatus | None = None,
    ) -> list[AgentInboxItem]:
        where: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            where.append("agent_id = %s")
            params.append(agent_id)
        if status is not None:
            where.append("status = %s")
            params.append(status)
        sql = "SELECT payload FROM agent_inbox_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY priority DESC, created_at, inbox_item_id"
        rows = self._fetchall(sql, tuple(params))
        return [AgentInboxItem.model_validate_json(row["payload"]) for row in rows]

    def save_agent_personal_profile(self, profile: AgentPersonalProfile) -> None:
        self._execute_upsert(
            "agent_personal_profiles",
            ["agent_id", "payload"],
            (profile.agent_id, profile.model_dump_json()),
        )

    def get_agent_personal_profile(self, agent_id: str) -> AgentPersonalProfile | None:
        row = self._fetchone("SELECT payload FROM agent_personal_profiles WHERE agent_id = %s", (agent_id,))
        return None if row is None else AgentPersonalProfile.model_validate_json(row["payload"])

    def list_agent_personal_profiles(self) -> list[AgentPersonalProfile]:
        rows = self._fetchall("SELECT payload FROM agent_personal_profiles ORDER BY agent_id")
        return [AgentPersonalProfile.model_validate_json(row["payload"]) for row in rows]

    def save_skill_definition(self, skill: SkillDefinition) -> None:
        self._execute_upsert(
            "skill_definitions",
            ["skill_id", "name", "status", "payload"],
            (skill.skill_id, skill.name, skill.status, skill.model_dump_json()),
        )

    def get_skill_definition(self, skill_id: str) -> SkillDefinition | None:
        row = self._fetchone("SELECT payload FROM skill_definitions WHERE skill_id = %s", (skill_id,))
        return None if row is None else SkillDefinition.model_validate_json(row["payload"])

    def list_skill_definitions(self) -> list[SkillDefinition]:
        rows = self._fetchall("SELECT payload FROM skill_definitions ORDER BY name, skill_id")
        return [SkillDefinition.model_validate_json(row["payload"]) for row in rows]

    def save_skill_assignment(self, assignment: SkillAssignment) -> None:
        self._execute_upsert(
            "skill_assignments",
            ["assignment_id", "skill_id", "target_type", "target_id", "enabled", "payload"],
            (
                assignment.assignment_id,
                assignment.skill_id,
                assignment.target_type,
                assignment.target_id,
                1 if assignment.enabled else 0,
                assignment.model_dump_json(),
            ),
        )

    def get_skill_assignment(self, assignment_id: str) -> SkillAssignment | None:
        row = self._fetchone("SELECT payload FROM skill_assignments WHERE assignment_id = %s", (assignment_id,))
        return None if row is None else SkillAssignment.model_validate_json(row["payload"])

    def list_skill_assignments(self) -> list[SkillAssignment]:
        rows = self._fetchall(
            "SELECT payload FROM skill_assignments ORDER BY target_type, target_id, skill_id, assignment_id"
        )
        return [SkillAssignment.model_validate_json(row["payload"]) for row in rows]

    def save_skill_materialization(self, materialization: SkillMaterialization) -> None:
        self._execute_upsert(
            "skill_materializations",
            ["materialization_id", "skill_id", "agent_id", "task_id", "run_id", "created_at", "payload"],
            (
                materialization.materialization_id,
                materialization.skill_id,
                materialization.agent_id,
                materialization.task_id,
                materialization.run_id,
                materialization.created_at.isoformat(),
                materialization.model_dump_json(),
            ),
        )

    def list_skill_materializations(self) -> list[SkillMaterialization]:
        rows = self._fetchall("SELECT payload FROM skill_materializations ORDER BY created_at, materialization_id")
        return [SkillMaterialization.model_validate_json(row["payload"]) for row in rows]

    def save_task(self, task: TaskContract) -> None:
        self._execute_upsert("tasks", ["task_id", "payload"], (task.task_id, task.model_dump_json()))

    def get_task(self, task_id: str) -> TaskContract | None:
        row = self._fetchone("SELECT payload FROM tasks WHERE task_id = %s", (task_id,))
        return None if row is None else TaskContract.model_validate_json(row["payload"])

    def list_tasks(self) -> list[TaskContract]:
        rows = self._fetchall("SELECT payload FROM tasks ORDER BY task_id")
        return [TaskContract.model_validate_json(row["payload"]) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        with self._conn.cursor() as cursor:
            cursor.execute("DELETE FROM tasks WHERE task_id = %s", (task_id,))
            deleted = cursor.rowcount > 0
        self._conn.commit()
        return deleted

    def save_report(self, report: ReportContract) -> None:
        self._execute_upsert(
            "reports",
            ["report_id", "task_id", "payload"],
            (report.report_id, report.task_id, report.model_dump_json()),
        )

    def list_reports_by_task(self, task_id: str) -> list[ReportContract]:
        rows = self._fetchall("SELECT payload FROM reports WHERE task_id = %s ORDER BY report_id", (task_id,))
        return [ReportContract.model_validate_json(row["payload"]) for row in rows]

    def get_report(self, report_id: str) -> ReportContract | None:
        row = self._fetchone("SELECT payload FROM reports WHERE report_id = %s", (report_id,))
        return None if row is None else ReportContract.model_validate_json(row["payload"])

    def list_reports(self) -> list[ReportContract]:
        rows = self._fetchall("SELECT payload FROM reports ORDER BY report_id")
        return [ReportContract.model_validate_json(row["payload"]) for row in rows]

    def save_clarification(self, clarification: Clarification) -> None:
        self._execute_upsert(
            "clarifications",
            ["clarification_id", "task_id", "status", "payload"],
            (
                clarification.clarification_id,
                clarification.origin_task_id,
                clarification.status,
                clarification.model_dump_json(),
            ),
        )

    def get_clarification(self, clarification_id: str) -> Clarification | None:
        row = self._fetchone(
            "SELECT payload FROM clarifications WHERE clarification_id = %s", (clarification_id,)
        )
        return None if row is None else Clarification.model_validate_json(row["payload"])

    def list_clarifications(self) -> list[Clarification]:
        rows = self._fetchall("SELECT payload FROM clarifications ORDER BY clarification_id")
        return [Clarification.model_validate_json(row["payload"]) for row in rows]

    def save_event(self, event: Event) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO events (event_id, timestamp, task_id, payload) VALUES (%s, %s, %s, %s)",
                (event.event_id, event.timestamp.isoformat(), event.task_id, event.model_dump_json()),
            )
        self._conn.commit()

    def list_events(self) -> list[Event]:
        rows = self._fetchall("SELECT payload FROM events ORDER BY sequence")
        return [Event.model_validate_json(row["payload"]) for row in rows]

    def list_events_after(self, sequence: int = 0, *, limit: int = 500) -> list[SequencedEvent]:
        rows = self._fetchall(
            "SELECT sequence, payload FROM events WHERE sequence > %s ORDER BY sequence LIMIT %s",
            (sequence, limit),
        )
        return [SequencedEvent(sequence=int(row["sequence"]), event=Event.model_validate_json(row["payload"])) for row in rows]

    def latest_event_sequence(self) -> int:
        row = self._fetchone("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM events")
        return int(row["sequence"])

    def save_artifact(self, artifact: Artifact) -> None:
        self._execute_upsert(
            "artifacts",
            ["artifact_id", "task_id", "payload"],
            (artifact.artifact_id, artifact.task_id, artifact.model_dump_json()),
        )

    def list_artifacts_by_task(self, task_id: str) -> list[Artifact]:
        rows = self._fetchall("SELECT payload FROM artifacts WHERE task_id = %s ORDER BY artifact_id", (task_id,))
        return [Artifact.model_validate_json(row["payload"]) for row in rows]

    def list_artifacts(self) -> list[Artifact]:
        rows = self._fetchall("SELECT payload FROM artifacts ORDER BY artifact_id")
        return [Artifact.model_validate_json(row["payload"]) for row in rows]

    def save_task_document(self, document: TaskDocument) -> None:
        self._execute_upsert(
            "task_documents",
            ["doc_id", "task_id", "payload"],
            (document.doc_id, document.task_id, document.model_dump_json()),
        )

    def get_task_document(self, doc_id: str) -> TaskDocument | None:
        row = self._fetchone("SELECT payload FROM task_documents WHERE doc_id = %s", (doc_id,))
        return None if row is None else TaskDocument.model_validate_json(row["payload"])

    def list_task_documents_by_task(self, task_id: str) -> list[TaskDocument]:
        rows = self._fetchall("SELECT payload FROM task_documents WHERE task_id = %s ORDER BY doc_id", (task_id,))
        return [TaskDocument.model_validate_json(row["payload"]) for row in rows]

    def list_task_documents(self) -> list[TaskDocument]:
        rows = self._fetchall("SELECT payload FROM task_documents ORDER BY doc_id")
        return [TaskDocument.model_validate_json(row["payload"]) for row in rows]

    def save_task_trace_export(self, trace: TaskTraceExport) -> None:
        self._execute_upsert(
            "task_trace_exports",
            ["trace_id", "task_id", "exported_at", "path", "payload"],
            (trace.trace_id, trace.task_id, trace.exported_at.isoformat(), trace.path, trace.model_dump_json()),
        )

    def get_task_trace_export(self, trace_id: str) -> TaskTraceExport | None:
        row = self._fetchone("SELECT payload FROM task_trace_exports WHERE trace_id = %s", (trace_id,))
        return None if row is None else TaskTraceExport.model_validate_json(row["payload"])

    def list_task_trace_exports_by_task(self, task_id: str) -> list[TaskTraceExport]:
        rows = self._fetchall(
            "SELECT payload FROM task_trace_exports WHERE task_id = %s ORDER BY exported_at, trace_id",
            (task_id,),
        )
        return [TaskTraceExport.model_validate_json(row["payload"]) for row in rows]

    def list_task_trace_exports(self) -> list[TaskTraceExport]:
        rows = self._fetchall("SELECT payload FROM task_trace_exports ORDER BY exported_at, trace_id")
        return [TaskTraceExport.model_validate_json(row["payload"]) for row in rows]

    def save_work_item(self, item: WorkItem) -> None:
        self._execute_upsert(
            "work_items",
            [
                "work_item_id",
                "status",
                "kind",
                "agent_id",
                "task_id",
                "priority",
                "model",
                "tool_name",
                "idempotency_key",
                "lease_owner",
                "lease_until",
                "created_at",
                "updated_at",
                "payload",
            ],
            (
                item.work_item_id,
                item.status,
                item.kind,
                item.agent_id,
                item.task_id,
                item.priority,
                item.model,
                item.tool_name,
                item.idempotency_key,
                item.lease_owner,
                item.lease_until.isoformat() if item.lease_until else None,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.model_dump_json(),
            ),
        )

    def get_work_item(self, work_item_id: str) -> WorkItem | None:
        row = self._fetchone("SELECT payload FROM work_items WHERE work_item_id = %s", (work_item_id,))
        return None if row is None else WorkItem.model_validate_json(row["payload"])

    def get_work_item_by_idempotency_key(self, idempotency_key: str) -> WorkItem | None:
        row = self._fetchone(
            "SELECT payload FROM work_items WHERE idempotency_key = %s ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        )
        return None if row is None else WorkItem.model_validate_json(row["payload"])

    def list_work_items(self, *, status: WorkItemStatus | None = None) -> list[WorkItem]:
        if status is None:
            rows = self._fetchall("SELECT payload FROM work_items ORDER BY created_at, work_item_id")
        else:
            rows = self._fetchall(
                "SELECT payload FROM work_items WHERE status = %s ORDER BY priority DESC, created_at, work_item_id",
                (status,),
            )
        return [WorkItem.model_validate_json(row["payload"]) for row in rows]

    def _execute_upsert(self, table: str, columns: list[str], values: tuple[object, ...]) -> None:
        placeholders = ", ".join(["%s"] * len(columns))
        update_columns = [column for column in columns[1:]]
        updates = ", ".join(f"{column} = VALUES({column})" for column in update_columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
        with self._conn.cursor() as cursor:
            cursor.execute(sql, values)
        self._conn.commit()

    def _fetchone(self, sql: str, params: tuple[object, ...] = ()) -> dict[str, Any] | None:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def _fetchall(self, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def _database_name(value: str | Path) -> str:
    candidate = str(value)
    if candidate.endswith((".sqlite", ".sqlite3", ".db")):
        candidate = Path(candidate).stem
    candidate = re.sub(r"[^0-9A-Za-z_]+", "_", candidate).strip("_")
    return candidate or "workforce_runtime"
