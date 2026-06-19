from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from workforce_runtime.core import Artifact, ReportContract, UsageCost
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_runtime_initializes_org_and_creates_task(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        organization = runtime.initialize_org(EXAMPLE_ORG)

        assert organization.company.name == "Demo Workforce"
        assert runtime.get_agent("codex_worker").name == "Codex Worker"

        task = runtime.create_task(
            title="Fix failing parser test",
            objective="Fix the failing parser test and report evidence.",
            assign_to="codex_worker",
        )

        assert task.task_id == "task_001"
        assert task.status == "assigned"
        assert runtime.require_task("task_001") == task
        assigned_agent = runtime.get_agent("codex_worker")
        assert assigned_agent.status == "busy"
        assert assigned_agent.current_task_ids == ["task_001"]

        events = runtime.store.list_events()
        assert [event.event_type for event in events] == [
            "org_initialized",
            "task_created",
            "task_assigned",
        ]


def test_runtime_updates_task_and_registers_report_and_artifact(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Produce design note",
            objective="Write a no-tools architecture note.",
            assign_to="codex_worker",
        )

        updated = runtime.update_task_status(task.task_id, status="completed", actor_id="codex_worker")
        assert updated.status == "completed"
        released_agent = runtime.get_agent("codex_worker")
        assert released_agent.status == "idle"
        assert released_agent.current_task_ids == []

        report = ReportContract(
            report_id="report_001",
            from_agent_id="codex_worker",
            to_agent_id="engineering_manager",
            task_id=task.task_id,
            summary="Completed the design note.",
            status="completed",
            work_done=["Wrote the note"],
            evidence=[],
            risks=[],
            blockers=[],
            confidence=0.9,
            cost=UsageCost(tokens_used=100, runtime_seconds=10, tool_calls=0),
            next_action="Ready for review.",
            requires_decision=False,
            alignment_check="Aligned with objective.",
        )
        runtime.register_report(report)

        artifact = Artifact(
            artifact_id="artifact_001",
            task_id=task.task_id,
            agent_id="codex_worker",
            type="design_doc",
            path="artifacts/task_001/design.md",
        )
        runtime.register_artifact(artifact)

        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "task_status_updated" in event_types
        assert "report_registered" in event_types
        assert "artifact_registered" in event_types
        assert "manager_review_created" in event_types
        assert "manager_review_decided" in event_types


def test_runtime_records_streaming_agent_output(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Plan implementation",
            objective="Have the manager produce a short plan.",
            assign_to="engineering_manager",
        )

        runtime.record_agent_run_started(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            adapter="openrouter",
            model="openai/gpt-oss-120b:free",
        )
        runtime.record_agent_output(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            stream="assistant",
            text="Assign parser work to codex_worker.",
        )
        runtime.record_agent_run_finished(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            status="completed",
            usage={"tokens_used": 12},
        )

        events = runtime.store.list_events()

    event_types = [event.event_type for event in events]
    assert "agent_run_started" in event_types
    assert "agent_output" in event_types
    assert "agent_run_finished" in event_types


def test_runtime_rejects_unknown_assignee(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        try:
            runtime.create_task(title="Bad task", objective="Bad assignment", assign_to="missing")
        except ValueError as exc:
            assert "unknown agent" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_task_cli_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"

    init = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "init",
            "--org",
            str(EXAMPLE_ORG),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Initialized Demo Workforce with 6 agents." in init.stdout

    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "task",
            "create",
            "--title",
            "Fix failing test",
            "--objective",
            "Fix the failing parser test",
            "--assign-to",
            "codex_worker",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    task_payload = json.loads(create.stdout)
    assert task_payload["task_id"] == "task_001"
    assert task_payload["status"] == "assigned"

    task_list = subprocess.run(
        [sys.executable, "-m", "workforce_runtime", "--db", str(db_path), "task", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "task_001\tassigned\tcodex_worker\tFix failing test" in task_list.stdout

    task_show = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "task",
            "show",
            "task_001",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(task_show.stdout)["objective"] == "Fix the failing parser test"

    with WorkforceRuntime(db_path) as runtime:
        assert [event.event_type for event in runtime.store.list_events()] == [
            "org_initialized",
            "task_created",
            "task_assigned",
        ]
