from __future__ import annotations

from pathlib import Path

from workforce_runtime.core import Artifact, ReportContract, UsageCost
from workforce_runtime.dashboard import render_agent_trajectories, render_event_replay, render_text_dashboard
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_text_dashboard_shows_phase_9_sections(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        active_task = runtime.create_task(
            title="Investigate parser",
            objective="Inspect parser failure.",
            assign_to="codex_worker",
        )
        completed_task = runtime.create_task(
            title="Write summary",
            objective="Produce a no-tools summary.",
            assign_to="claude_worker",
        )
        runtime.update_task_status(completed_task.task_id, status="completed", actor_id="claude_worker")

        runtime.register_report(
            ReportContract(
                report_id="report_001",
                from_agent_id="claude_worker",
                to_agent_id="engineering_manager",
                task_id=completed_task.task_id,
                summary="No-tools summary completed.",
                status="completed",
                work_done=["Wrote summary"],
                evidence=[],
                risks=[],
                blockers=[],
                confidence=0.8,
                cost=UsageCost(tokens_used=123, runtime_seconds=4, tool_calls=0),
                next_action="Approve summary.",
                requires_decision=False,
                alignment_check="Aligned.",
            )
        )
        runtime.register_artifact(
            Artifact(
                artifact_id="artifact_001",
                task_id=completed_task.task_id,
                agent_id="claude_worker",
                type="report",
                path="artifacts/task_002/report.md",
            )
        )
        runtime.record_event(
            event_type="permission_requested",
            actor_id="codex_worker",
            task_id=active_task.task_id,
            payload={"permission": "write_branch"},
        )

        dashboard = render_text_dashboard(runtime.store)
        replay = render_event_replay(runtime.store)
        trajectories = render_agent_trajectories(runtime.store)

    assert "Company:\n  Demo Workforce" in dashboard
    assert "Company Goal:\n  Build software using AI workers." in dashboard
    assert "Budget:" in dashboard
    assert "Tokens used: 123" in dashboard
    assert "Headcount: 6 / 8" in dashboard
    assert "Organization:" in dashboard
    assert "Codex Worker" in dashboard
    assert "Active Agents:" in dashboard
    assert f"Codex Worker  busy  {active_task.task_id}" in dashboard
    assert "Idle Agents:" in dashboard
    assert "Claude Worker  idle  -" in dashboard
    assert "Active Tasks:" in dashboard
    assert f"{active_task.task_id}  Investigate parser  assigned  Codex Worker" in dashboard
    assert "Completed Tasks:" in dashboard
    assert f"{completed_task.task_id}  Write summary  completed  Claude Worker" in dashboard
    assert "Recent Reports:" in dashboard
    assert "No-tools summary completed." in dashboard
    assert "Recent Artifacts:" in dashboard
    assert "artifact_001  report  artifacts/task_002/report.md" in dashboard
    assert "Decision Inbox:" in dashboard
    assert "codex_worker requested write_branch" in dashboard
    assert "Worker Performance:" in dashboard
    assert "Agent Runs:" in dashboard
    assert "Live Agent Output:" in dashboard
    assert "Claude Worker: tasks=1, completed=1, reports=1, artifacts=1" in dashboard

    assert "Event Replay" in replay
    assert "permission_requested" in replay
    assert "Agent Trajectories" in trajectories
    assert "Claude Worker" in trajectories
    assert f"reported report_001 on {completed_task.task_id}: completed" in trajectories
