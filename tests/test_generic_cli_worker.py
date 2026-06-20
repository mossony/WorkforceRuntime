from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from workforce_runtime.dashboard import render_text_dashboard
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import GenericCLIWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")
MOCK_WORKER = Path("examples/mock_worker/mock_worker.py").resolve()


def test_generic_cli_worker_spawns_mock_worker_and_records_report(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="No-tools design summary",
            objective="Produce a short architecture summary without external tools.",
            assign_to="codex_worker",
        )
        worker = GenericCLIWorker([sys.executable, str(MOCK_WORKER)], timeout_seconds=10)
        run = worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        assert run.returncode == 0
        assert run.stdout_path.read_text().strip().startswith('{"ok": true')
        assert run.stderr_path.read_text() == ""
        assert run.task_contract_path.exists()
        assert {path.name for path in worker.collect_artifacts(run.run_id)} >= {
            "task_contract.json",
            "stdout.log",
            "stderr.log",
        }
        assert worker.get_usage(run.run_id) == {
            "tokens_used": 0,
            "runtime_seconds": 0,
            "tool_calls": 0,
        }

        reports = runtime.store.list_reports_by_task(task.task_id)
        assert len(reports) == 1
        assert reports[0].from_agent_id == "codex_worker"
        assert reports[0].summary == "Mock worker completed: No-tools design summary"

        completed_task = runtime.require_task(task.task_id)
        assert completed_task.status == "completed"
        dashboard = render_text_dashboard(runtime.store)
        assert "Company:" in dashboard
        assert "Organization:" in dashboard
        assert "Active Agents:" in dashboard
        assert "Idle Agents:" in dashboard
        assert "Recent Reports:" in dashboard
        assert "Recent Artifacts:" in dashboard
        assert "Decision Inbox:" in dashboard
        assert "Worker Performance:" in dashboard
        assert "Agent Runs:" in dashboard
        assert "Live Agent Output:" in dashboard
        assert "Completed Tasks:" in dashboard
        assert f"{task.task_id}  No-tools design summary  completed  Codex Worker" in dashboard
        events = runtime.store.list_events()
        assert any(event.event_type == "report_registered" and event.task_id == task.task_id for event in events)
        assert "worker_output" in [event.event_type for event in events]
        assert "worker_run_started" in [event.event_type for event in events]
        assert "worker_run_finished" in [event.event_type for event in events]


def test_dashboard_cli_shows_completed_mock_worker_task(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Tool task",
            objective="Run the mock worker through MCP.",
            assign_to="codex_worker",
        )
        GenericCLIWorker([sys.executable, str(MOCK_WORKER)], timeout_seconds=10).start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

    result = subprocess.run(
        [sys.executable, "-m", "workforce_runtime", "--db", str(db_path), "dashboard"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Workforce Runtime" in result.stdout
    assert "Completed Tasks:" in result.stdout
    assert "Tool task  completed  Codex Worker" in result.stdout
