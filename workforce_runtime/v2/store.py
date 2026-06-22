from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from workforce_runtime.v2.models import (
    AuditRecord,
    Decision,
    Experiment,
    Finding,
    NormalizedEvent,
    OrganizationChangeProposal,
    OrganizationSnapshot,
    OrganizationState,
    SimulationResult,
    WorkGraph,
)

T = TypeVar("T", bound=BaseModel)


class V2SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> V2SQLiteStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS v2_objects (
                kind TEXT NOT NULL,
                object_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (kind, object_id)
            );

            CREATE TABLE IF NOT EXISTS v2_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                organization_id TEXT NOT NULL,
                project_id TEXT,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (source, source_event_id)
            );

            CREATE TABLE IF NOT EXISTS v2_audit_records (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id TEXT NOT NULL UNIQUE,
                organization_id TEXT NOT NULL,
                action TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def save_state(self, state: OrganizationState) -> None:
        self.save_object("organization_state", state.organization.id, state)

    def load_state(self, organization_id: str) -> OrganizationState | None:
        return self.get_object("organization_state", organization_id, OrganizationState)

    def save_event(self, event: NormalizedEvent) -> bool:
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO v2_events (
                event_id,
                organization_id,
                project_id,
                event_type,
                occurred_at,
                observed_at,
                source,
                source_event_id,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.organization_id,
                event.project_id,
                event.event_type,
                event.occurred_at.isoformat(),
                event.observed_at.isoformat(),
                event.source,
                event.source_event_id,
                event.model_dump_json(),
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def save_events(self, events: list[NormalizedEvent]) -> int:
        inserted = 0
        for event in events:
            if self.save_event(event):
                inserted += 1
        return inserted

    def list_events(self, organization_id: str | None = None) -> list[NormalizedEvent]:
        if organization_id is None:
            rows = self._conn.execute("SELECT payload FROM v2_events ORDER BY occurred_at, sequence").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload FROM v2_events WHERE organization_id = ? ORDER BY occurred_at, sequence",
                (organization_id,),
            ).fetchall()
        return [NormalizedEvent.model_validate_json(row["payload"]) for row in rows]

    def latest_event_cursor(self) -> str | None:
        row = self._conn.execute("SELECT MAX(sequence) AS sequence FROM v2_events").fetchone()
        if row is None or row["sequence"] is None:
            return None
        return str(row["sequence"])

    def save_snapshot(self, snapshot: OrganizationSnapshot) -> None:
        self.save_object("snapshot", snapshot.id, snapshot)

    def get_snapshot(self, snapshot_id: str) -> OrganizationSnapshot | None:
        return self.get_object("snapshot", snapshot_id, OrganizationSnapshot)

    def list_snapshots(self, organization_id: str | None = None) -> list[OrganizationSnapshot]:
        snapshots = self.list_objects("snapshot", OrganizationSnapshot)
        if organization_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.organization_id == organization_id]
        return sorted(snapshots, key=lambda snapshot: snapshot.created_at)

    def save_work_graph(self, graph: WorkGraph) -> None:
        self.save_object("work_graph", graph.organization_id, graph)

    def load_work_graph(self, organization_id: str) -> WorkGraph | None:
        return self.get_object("work_graph", organization_id, WorkGraph)

    def save_finding(self, finding: Finding) -> None:
        self.save_object("finding", finding.id, finding)

    def list_findings(self, organization_id: str | None = None) -> list[Finding]:
        findings = self.list_objects("finding", Finding)
        if organization_id is not None:
            findings = [finding for finding in findings if finding.organization_id == organization_id]
        return findings

    def save_proposal(self, proposal: OrganizationChangeProposal) -> None:
        self.save_object("proposal", proposal.id, proposal)

    def get_proposal(self, proposal_id: str) -> OrganizationChangeProposal | None:
        return self.get_object("proposal", proposal_id, OrganizationChangeProposal)

    def list_proposals(self) -> list[OrganizationChangeProposal]:
        return self.list_objects("proposal", OrganizationChangeProposal)

    def save_simulation_result(self, result: SimulationResult) -> None:
        self.save_object("simulation", result.id, result)

    def list_simulation_results(self) -> list[SimulationResult]:
        return self.list_objects("simulation", SimulationResult)

    def save_decision(self, decision: Decision) -> None:
        self.save_object("decision", decision.id, decision)

    def get_decision(self, decision_id: str) -> Decision | None:
        return self.get_object("decision", decision_id, Decision)

    def list_decisions(self) -> list[Decision]:
        return self.list_objects("decision", Decision)

    def save_experiment(self, experiment: Experiment) -> None:
        self.save_object("experiment", experiment.id, experiment)

    def list_experiments(self) -> list[Experiment]:
        return self.list_objects("experiment", Experiment)

    def save_audit_record(self, record: AuditRecord) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO v2_audit_records (
                audit_id,
                organization_id,
                action,
                object_type,
                object_id,
                occurred_at,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.organization_id,
                record.action,
                record.object_type,
                record.object_id,
                record.occurred_at.isoformat(),
                record.model_dump_json(),
            ),
        )
        self._conn.commit()

    def list_audit_records(self, organization_id: str | None = None) -> list[AuditRecord]:
        if organization_id is None:
            rows = self._conn.execute("SELECT payload FROM v2_audit_records ORDER BY sequence").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload FROM v2_audit_records WHERE organization_id = ? ORDER BY sequence",
                (organization_id,),
            ).fetchall()
        return [AuditRecord.model_validate_json(row["payload"]) for row in rows]

    def save_object(self, kind: str, object_id: str, payload: BaseModel) -> None:
        self._conn.execute(
            """
            INSERT INTO v2_objects (kind, object_id, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(kind, object_id) DO UPDATE SET payload = excluded.payload
            """,
            (kind, object_id, payload.model_dump_json()),
        )
        self._conn.commit()

    def get_object(self, kind: str, object_id: str, model: type[T]) -> T | None:
        row = self._conn.execute(
            "SELECT payload FROM v2_objects WHERE kind = ? AND object_id = ?",
            (kind, object_id),
        ).fetchone()
        if row is None:
            return None
        return model.model_validate_json(row["payload"])

    def list_objects(self, kind: str, model: type[T]) -> list[T]:
        rows = self._conn.execute(
            "SELECT payload FROM v2_objects WHERE kind = ? ORDER BY object_id",
            (kind,),
        ).fetchall()
        return [model.model_validate_json(row["payload"]) for row in rows]
