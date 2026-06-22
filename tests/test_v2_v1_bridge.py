from __future__ import annotations

import subprocess
import sys

from workforce_runtime.core import AgentProfile, Budget, Event, TaskContract
from workforce_runtime.storage import SQLiteStore
from workforce_runtime.v2.v1_bridge import analyze_v1_runtime


def _seed_v1_run(db_path) -> str:
    with SQLiteStore(db_path) as store:
        manager = AgentProfile(
            id="manager",
            name="Manager",
            role="Engineering Manager",
            department="Engineering",
            worker_type="generic_cli",
            permissions=["delegate_task", "report"],
        )
        worker = AgentProfile(
            id="worker",
            name="Worker",
            role="Implementer",
            department="Engineering",
            manager_id="manager",
            worker_type="codex",
            permissions=["read_repo", "write_branch", "run_tests", "submit_artifact", "report"],
        )
        store.save_agent(manager)
        store.save_agent(worker)
        task = TaskContract(
            task_id="task_bridge",
            title="Implement parser fix",
            objective="Fix parser behavior and report results.",
            assigned_to="worker",
            assigned_by="manager",
            budget=Budget(max_tokens=1000),
            status="completed",
        )
        store.save_task(task)
        events = [
            Event(event_id="event_1", event_type="task_created", actor_id="manager", task_id=task.task_id, payload={"assigned_to": "worker"}),
            Event(event_id="event_2", event_type="task_assigned", actor_id="manager", task_id=task.task_id, payload={"assigned_to": "worker"}),
            Event(event_id="event_3", event_type="task_status_updated", actor_id="worker", task_id=task.task_id, payload={"status": "in_progress"}),
            Event(event_id="event_4", event_type="worker_run_started", actor_id="worker", task_id=task.task_id, payload={"run_id": "run_1"}),
            Event(event_id="event_5", event_type="artifact_registered", actor_id="worker", task_id=task.task_id, payload={"artifact_id": "artifact_1", "type": "git_diff"}),
            Event(event_id="event_6", event_type="report_registered", actor_id="worker", task_id=task.task_id, payload={"report_id": "report_1", "status": "completed"}),
            Event(event_id="event_7", event_type="manager_review_created", actor_id="manager", task_id="review_task", payload={"reviewed_task_id": task.task_id}),
            Event(
                event_id="event_8",
                event_type="manager_review_decided",
                actor_id="manager",
                task_id="review_task",
                payload={"reviewed_task_id": task.task_id, "accepted": True, "decision": "accept"},
            ),
            Event(event_id="event_9", event_type="task_status_updated", actor_id="worker", task_id=task.task_id, payload={"status": "completed"}),
        ]
        for event in events:
            store.save_event(event)
    return task.task_id


def _seed_failed_worker_run(db_path) -> str:
    with SQLiteStore(db_path) as store:
        manager = AgentProfile(
            id="manager",
            name="Manager",
            role="Engineering Manager",
            department="Engineering",
            worker_type="generic_cli",
            permissions=["delegate_task", "report"],
        )
        worker = AgentProfile(
            id="worker",
            name="Worker",
            role="Implementer",
            department="Engineering",
            manager_id="manager",
            worker_type="codex",
            permissions=["read_repo", "write_branch", "run_tests", "submit_artifact", "report"],
        )
        store.save_agent(manager)
        store.save_agent(worker)
        task = TaskContract(
            task_id="task_failed_bridge",
            title="Fix Symbol slots",
            objective="Fix Symbol __dict__ regression.",
            assigned_to="worker",
            assigned_by="manager",
            budget=Budget(max_tokens=1000),
            status="failed",
        )
        store.save_task(task)
        events = [
            Event(event_id="failed_1", event_type="task_created", actor_id="manager", task_id=task.task_id, payload={"assigned_to": "worker"}),
            Event(event_id="failed_2", event_type="task_status_updated", actor_id="worker", task_id=task.task_id, payload={"status": "in_progress"}),
            Event(event_id="failed_3", event_type="worker_run_started", actor_id="worker", task_id=task.task_id, payload={"run_id": "run_failed"}),
            Event(
                event_id="failed_4",
                event_type="worker_output",
                actor_id="worker",
                task_id=task.task_id,
                payload={
                    "run_id": "run_failed",
                    "stream": "stdout",
                    "text": "MRO includes Printable; has __dict__? True; class Printable lacks __slots__.",
                },
            ),
            Event(
                event_id="failed_5",
                event_type="worker_output",
                actor_id="worker",
                task_id=task.task_id,
                payload={
                    "run_id": "run_failed",
                    "stream": "stdout",
                    "text": '{"exit_code":127,"status":"failed"} unrecognized flag No such file or directory',
                },
            ),
            Event(event_id="failed_6", event_type="worker_run_finished", actor_id="worker", task_id=task.task_id, payload={"run_id": "run_failed", "returncode": -15}),
            Event(event_id="failed_7", event_type="task_status_updated", actor_id="worker", task_id=task.task_id, payload={"status": "failed"}),
        ]
        for event in events:
            store.save_event(event)
    return task.task_id


def test_v2_analyzes_v1_runtime_run_and_exports_artifacts(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    task_id = _seed_v1_run(db_path)
    export_dir = tmp_path / "analysis"

    result = analyze_v1_runtime(v1_db_path=db_path, task_id=task_id, export_dir=export_dir)

    assert result.task_id == task_id
    assert len(result.normalized_events) == 9
    assert any(event.event_type == "report_submitted" for event in result.normalized_events)
    assert any(event.event_type == "review_completed" for event in result.normalized_events)
    assert result.metrics
    assert any(finding.finding_type == "manager_review_overhead" for finding in result.findings)
    assert result.recommendations
    assert (export_dir / "v2_analysis.json").exists()
    assert (export_dir / "recommendations.md").read_text().strip()


def test_v2_analyze_v1_run_cli_outputs_recommendations(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    task_id = _seed_v1_run(db_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "v2",
            "analyze-v1-run",
            "--task-id",
            task_id,
            "--export-dir",
            str(tmp_path / "analysis"),
        ],
        cwd="/Users/boyangwan/Desktop/WorkforceRuntime",
        check=True,
        capture_output=True,
        text=True,
    )

    assert "V2 analysis of V1 run" in completed.stdout
    assert "Recommendations:" in completed.stdout


def test_v2_analyzes_failed_worker_run_with_actionable_findings(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    task_id = _seed_failed_worker_run(db_path)

    result = analyze_v1_runtime(v1_db_path=db_path, task_id=task_id)

    finding_types = {finding.finding_type for finding in result.findings}
    assert "worker_execution_failed" in finding_types
    assert "diagnosis_without_patch" in finding_types
    assert "tool_usage_friction" in finding_types
    recommendations = "\n".join(result.recommendations)
    assert "manager checkpoint" in recommendations
    assert "investigator, implementer, and reviewer" in recommendations
