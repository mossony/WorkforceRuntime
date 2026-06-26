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

        assert task.task_id.startswith("task_001_fix_failing_parser_test_")
        assert task.status == "assigned"
        assert runtime.require_task(task.task_id) == task
        assigned_agent = runtime.get_agent("codex_worker")
        assert assigned_agent.status == "busy"
        assert assigned_agent.current_task_ids == [task.task_id]

        events = runtime.store.list_events()
        event_types = [event.event_type for event in events]
        assert event_types[:3] == ["org_initialized", "task_created", "task_assigned"]
        assert "agent_inbox_item_enqueued" in event_types
        assert runtime.list_agent_inbox_items(agent_id="codex_worker", status="queued")[0].kind == "assignment"


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
        assert "agent_inbox_item_enqueued" in event_types
        inbox_items = runtime.list_agent_inbox_items(agent_id="engineering_manager", status="queued")
        assert any(item.kind == "report_review" and item.payload["report_id"] == "report_001" for item in inbox_items)


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


def test_runtime_auto_replaces_unavailable_agent_model_and_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        agent = runtime.get_agent("engineering_manager")
        assert agent is not None
        runtime.store.save_agent(
            agent.model_copy(
                update={
                    "model": "deepseek-ai/deepseek-v4-pro",
                    "system_prompt": "Assigned model: deepseek-ai/deepseek-v4-pro.",
                }
            )
        )
        task = runtime.create_task(
            title="Recover bad model",
            objective="Trigger unavailable model recovery.",
            assign_to="engineering_manager",
        )

        updated = runtime.auto_replace_unavailable_agent_model(
            agent_id="engineering_manager",
            failed_model="deepseek-ai/deepseek-v4-pro",
            error='HTTP 400: {"detail":"Function id abc: DEGRADED function cannot be invoked"}',
            task_id=task.task_id,
        )

        assert updated is not None
        assert updated.model == "openai/gpt-oss-120b:free"
        stored = runtime.get_agent("engineering_manager")
        assert stored is not None
        assert stored.model == "openai/gpt-oss-120b:free"
        assert "Assigned model: openai/gpt-oss-120b:free." in stored.system_prompt
        assert "deepseek-ai/deepseek-v4-pro" not in stored.system_prompt
        events = [event for event in runtime.store.list_events() if event.event_type == "agent_model_auto_replaced"]
        assert events[-1].payload["old_model"] == "deepseek-ai/deepseek-v4-pro"
        assert events[-1].payload["new_model"] == "openai/gpt-oss-120b:free"


def test_runtime_exports_complete_task_trace_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    artifact_path = tmp_path / "design.md"
    artifact_path.write_text("# Design\n\nTrace this file.")

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Produce design note",
            objective="Write a short design note and report evidence.",
            assign_to="codex_worker",
        )
        runtime.record_agent_run_started(
            run_id="codex_run_001",
            task_id=task.task_id,
            actor_id="codex_worker",
            adapter="codex",
            model="poolside/laguna-m.1:free",
        )
        runtime.record_agent_output(
            run_id="codex_run_001",
            task_id=task.task_id,
            actor_id="codex_worker",
            stream="assistant",
            text="Writing the design note.",
        )
        runtime.record_agent_run_finished(
            run_id="codex_run_001",
            task_id=task.task_id,
            actor_id="codex_worker",
            status="completed",
            usage={"total_tokens": 42},
        )
        work_item = runtime.enqueue_work_item(
            actor_id="system",
            agent_id="codex_worker",
            kind="llm_request",
            task_id=task.task_id,
            payload={"prompt": "draft design note"},
            model="openai/gpt-oss-120b:free",
        )
        runtime.claim_work_items(lease_owner="trace-test", limit=1)
        runtime.upsert_task_document(
            actor_id="codex_worker",
            task_id=task.task_id,
            title="Requirements",
            doc_type="requirements",
            content="Write a short note and include evidence.",
        )
        runtime.register_artifact(
            Artifact(
                artifact_id="artifact_001",
                task_id=task.task_id,
                agent_id="codex_worker",
                type="design_doc",
                path=str(artifact_path),
            )
        )
        runtime.register_report(
            ReportContract(
                report_id="report_001",
                from_agent_id="codex_worker",
                to_agent_id="engineering_manager",
                task_id=task.task_id,
                summary="Completed the design note.",
                status="completed",
                work_done=["Wrote the note"],
                evidence=[{"type": "design_doc", "path": str(artifact_path)}],
                risks=[],
                blockers=[],
                confidence=0.9,
                cost=UsageCost(tokens_used=42, runtime_seconds=2, tool_calls=1),
                next_action="Ready for review.",
                requires_decision=False,
                alignment_check="Aligned with objective.",
            )
        )

        trace = runtime.export_task_trace(task.task_id, workspace=tmp_path / "manual_traces")
        stored = runtime.store.get_task_trace_export(trace.trace_id)
        exports = runtime.store.list_task_trace_exports_by_task(task.task_id)

    assert stored == trace
    assert exports[-1].trace_id == trace.trace_id
    assert Path(trace.path).exists()
    written = json.loads(Path(trace.path).read_text())
    payload = written["payload"]
    assert payload["task_id"] == task.task_id
    assert task.task_id in payload["scope"]["task_ids"]
    assert any(item["task_id"] != task.task_id for item in payload["tasks"])
    assert any(event["event_type"] == "agent_output" for event in payload["events"])
    assert any(run["run_id"] == "codex_run_001" and run["usage"]["total_tokens"] == 42 for run in payload["agent_runs"])
    assert payload["documents"][0]["doc_type"] == "requirements"
    assert payload["summary"]["work_item_count"] == 1
    assert payload["work_items"][0]["work_item_id"] == work_item.work_item_id
    assert payload["work_items"][0]["status"] == "leased"
    assert payload["reports"][0]["report_id"] == "report_001"
    assert payload["artifacts"][0]["path"] == str(artifact_path)
    assert any(file["path"] == str(artifact_path) and "Trace this file." in file["content"] for file in payload["files"])


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
    assert task_payload["task_id"].startswith("task_001_fix_failing_test_")
    assert task_payload["status"] == "assigned"
    task_id = task_payload["task_id"]

    task_list = subprocess.run(
        [sys.executable, "-m", "workforce_runtime", "--db", str(db_path), "task", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert f"{task_id}\tassigned\tcodex_worker\tFix failing test" in task_list.stdout

    task_show = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "task",
            "show",
            task_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(task_show.stdout)["objective"] == "Fix the failing parser test"

    with WorkforceRuntime(db_path) as runtime:
        event_types = [event.event_type for event in runtime.store.list_events()]
        assert event_types[:3] == ["org_initialized", "task_created", "task_assigned"]
        assert "agent_inbox_item_enqueued" in event_types
