from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from workforce_runtime.core import ReportContract, UsageCost
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def make_report(task_id: str, *, status: str = "completed", confidence: float = 0.9) -> ReportContract:
    return ReportContract(
        report_id="report_001",
        from_agent_id="codex_worker",
        to_agent_id="engineering_manager",
        task_id=task_id,
        summary="Worker completed the implementation.",
        status=status,
        work_done=["Implemented change", "Ran tests"],
        evidence=[{"type": "test_log", "path": "artifacts/task_001/pytest.log"}],
        risks=[],
        blockers=[],
        confidence=confidence,
        cost=UsageCost(tokens_used=1000, runtime_seconds=30, tool_calls=5),
        next_action="Ready for manager review.",
        requires_decision=False,
        alignment_check="Matches acceptance criteria.",
    )


def test_register_report_creates_manager_review_task_and_accepts(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Fix parser",
            objective="Fix parser and provide tests.",
            assign_to="codex_worker",
            acceptance_criteria=["Tests pass"],
        )
        runtime.update_task_status(task.task_id, status="completed", actor_id="codex_worker")

        runtime.register_report(make_report(task.task_id))

        tasks = runtime.list_tasks()
        review_tasks = [candidate for candidate in tasks if candidate.parent_task_id == task.task_id]
        assert len(review_tasks) == 1
        assert review_tasks[0].assigned_to == "engineering_manager"
        assert review_tasks[0].context_refs == ["report:report_001", f"task:{task.task_id}"]
        assert review_tasks[0].status == "assigned"
        assert runtime.require_task(task.task_id).status == "completed"
        inbox_items = runtime.list_agent_inbox_items(agent_id="engineering_manager", status="queued")
        assert len(inbox_items) == 1
        assert inbox_items[0].kind == "report_review"
        assert inbox_items[0].payload["report_id"] == "report_001"
        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "manager_review_created" in event_types
        assert "agent_inbox_item_enqueued" in event_types


def test_manager_review_requires_explicit_decision(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Fix parser",
            objective="Fix parser and provide tests.",
            assign_to="codex_worker",
        )
        runtime.register_report(make_report(task.task_id, status="failed", confidence=0.3))

        with pytest.raises(ValueError, match="explicit decision"):
            runtime.review_report("report_001", reviewer_id="engineering_manager")


def test_manager_review_rejects_failed_report_with_explicit_decision(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Fix parser",
            objective="Fix parser and provide tests.",
            assign_to="codex_worker",
        )
        runtime.register_report(make_report(task.task_id, status="failed", confidence=0.3))

        assert runtime.require_task(task.task_id).status == "assigned"
        review_task = next(candidate for candidate in runtime.list_tasks() if candidate.parent_task_id == task.task_id)
        runtime.review_report("report_001", reviewer_id="engineering_manager", decision="reject")
        assert runtime.require_task(task.task_id).status == "failed"
        assert runtime.require_task(review_task.task_id).status == "completed"
        decision = next(event for event in runtime.store.list_events() if event.event_type == "manager_review_reject")
        assert decision.payload["report_id"] == "report_001"


def test_review_report_cli_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Fix parser",
            objective="Fix parser and provide tests.",
            assign_to="codex_worker",
        )
        runtime.register_report(make_report(task.task_id))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "review",
            "report",
            "report_001",
            "--reviewer",
            "engineering_manager",
            "--decision",
            "accept",
            "--notes",
            "Looks good.",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["task_id"] == task.task_id
    assert payload["status"] == "completed"
