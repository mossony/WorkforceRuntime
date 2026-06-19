from __future__ import annotations

import sys
from pathlib import Path

from workforce_runtime.core import Budget, ReportContract, UsageCost
from workforce_runtime.dashboard import render_text_dashboard
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import GenericCLIWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_generic_worker_exceeding_runtime_budget_is_stopped_and_recorded(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Slow worker",
            objective="This worker should exceed runtime budget.",
            assign_to="codex_worker",
        )
        task.budget = Budget(max_runtime_seconds=1)
        runtime.store.save_task(task)

        worker = GenericCLIWorker([sys.executable, "-c", "import time; time.sleep(3)"])
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

        assert run.returncode == -1
        assert runtime.require_task(task.task_id).status == "failed"
        events = runtime.store.list_events()
        violation = next(event for event in events if event.event_type == "budget_violation")
        assert violation.payload["reason"] == "worker exceeded runtime budget"
        assert violation.payload["usage"]["runtime_seconds"] == 1

        dashboard = render_text_dashboard(runtime.store)
        assert "Budget Overruns:" in dashboard
        assert "task_001  codex_worker  worker exceeded runtime budget" in dashboard


def test_report_usage_over_budget_records_violation(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Expensive task",
            objective="Produce an expensive report.",
            assign_to="codex_worker",
        )
        task.budget = Budget(max_tokens=10, max_runtime_seconds=5, max_tool_calls=1)
        runtime.store.save_task(task)

        runtime.register_report(
            ReportContract(
                report_id="report_001",
                from_agent_id="codex_worker",
                to_agent_id="engineering_manager",
                task_id=task.task_id,
                summary="Completed with excessive cost.",
                status="completed",
                work_done=["Did work"],
                evidence=[],
                risks=[],
                blockers=[],
                confidence=0.9,
                cost=UsageCost(tokens_used=100, runtime_seconds=8, tool_calls=3),
                next_action="Review cost.",
                requires_decision=False,
                alignment_check="Aligned.",
            )
        )

        violations = [event for event in runtime.store.list_events() if event.event_type == "budget_violation"]
        assert len(violations) == 1
        assert "tokens" in violations[0].payload["reason"]
        assert "runtime_seconds" in violations[0].payload["reason"]
        assert "tool_calls" in violations[0].payload["reason"]
