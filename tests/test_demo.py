from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path

from workforce_runtime.server.demo import (
    build_large_scale_organization,
    run_large_org_scale_demo,
    run_sample_repo_fix_demo,
    run_simple_status_demo,
    run_web_research_demo,
)
from workforce_runtime.server.large_task_100 import (
    _apply_governor_design_actions,
    _organization_from_design_policy,
    build_large_task_100_organization,
    load_large_task_positions,
)
from workforce_runtime.server.runtime import WorkforceRuntime


def test_sample_repo_fix_demo_runs_no_tools_and_tool_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.sqlite"
    workspace = tmp_path / "sample_repo"

    output = run_sample_repo_fix_demo(db_path, workspace)

    assert "Workforce Runtime Demo: sample-repo-fix" in output
    assert "Human -> CEO -> VP Engineering -> Engineering Manager -> Codex Worker" in output
    assert "Company goal task: task_001" in output
    assert "VP delegation task: task_002" in output
    assert "No-tools task: task_003" in output
    assert "Tool task: task_005" in output
    assert "Worker Report:" in output
    assert "Fixed boolean parser handling and ran pytest." in output
    assert "Manager Review Inbox:" in output
    assert "'report_id':" in output
    assert "Diff:" in output
    assert "Test log:" in output
    assert "Final status: completed" in output
    assert "Workforce Runtime" in output
    tool_task_id = re.search(r"Tool task: (\S+)", output).group(1)
    assert (workspace / "artifacts" / tool_task_id / "pytest.log").exists()
    assert (workspace / "artifacts" / tool_task_id / "diff.patch").exists()
    assert "3 passed" in (workspace / "artifacts" / tool_task_id / "pytest.log").read_text()


def test_sample_repo_fix_demo_cli_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.sqlite"
    workspace = tmp_path / "sample_repo"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "demo",
            "sample-repo-fix",
            "--workspace",
            str(workspace),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Final status: completed" in result.stdout
    assert "No-tools task: task_003" in result.stdout
    assert "Tool task: task_005" in result.stdout
    assert "Recent Artifacts:" in result.stdout


def test_simple_status_demo_shows_model_routing_progress_and_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "simple.sqlite"
    workspace = tmp_path / "simple_workspace"

    output = run_simple_status_demo(db_path, workspace)

    assert "Workforce Runtime Demo: simple-status" in output
    assert "Managers: openai/gpt-oss-120b:free" in output
    assert "Terminal worker: poolside/laguna-xs.2:free" in output
    assert "Human -> CEO -> Product Manager -> Laguna Worker" in output
    assert "Progress Check:" in output
    assert "Created concise launch note artifact." in output
    assert "Live Dashboard Snapshots:" in output
    assert "Event Replay" in output
    assert "progress_check_requested" in output
    assert "Agent Trajectories" in output
    assert "Final status: completed" in output
    launch_task_id = re.search(r"Worker task: (\S+)", output).group(1)
    assert (workspace / "artifacts" / launch_task_id / "launch_note.md").exists()


def test_simple_status_demo_cli_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "simple.sqlite"
    workspace = tmp_path / "simple_workspace"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "demo",
            "simple-status",
            "--workspace",
            str(workspace),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Final status: completed" in result.stdout
    assert "Event Replay" in result.stdout


def test_web_research_demo_runs_with_tool_calls_and_artifact(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "web.sqlite"
    workspace = tmp_path / "web_workspace"
    source = tmp_path / "source.html"
    source.write_text(
        "<html><head><title>Example Domain Fixture</title></head>"
        "<body>example.com example.net example.org</body></html>"
    )
    monkeypatch.setenv("WORKFORCE_WEB_RESEARCH_URL", source.as_uri())

    output = run_web_research_demo(db_path, workspace)

    assert "Workforce Runtime Demo: web-research" in output
    assert "Human -> CEO -> VP Engineering -> Engineering Manager -> Codex Worker" in output
    assert "MCP tool-call events:" in output
    assert "Output stream events:" in output
    assert "Final status: completed" in output
    assert "Agent Runs:" in output
    assert "Live Agent Output:" in output
    worker_task_id = re.search(r"Worker task: (\S+)", output).group(1)
    summary = workspace / "artifacts" / worker_task_id / "web_research_summary.md"
    assert summary.exists()
    assert "Example Domain Fixture" in summary.read_text()


def test_large_scale_org_builder_creates_requested_headcount() -> None:
    organization = build_large_scale_organization(agent_count=120)

    assert organization.company.headcount_limit == 120
    assert len(organization.agents) == 120
    assert organization.require_agent("ceo").manager_id is None
    assert any(agent.manager_id == "ceo" for agent in organization.agents)
    manager_ids = {agent.manager_id for agent in organization.agents if agent.manager_id}
    workers = [agent for agent in organization.agents if agent.id not in manager_ids and agent.manager_id is not None]
    assert workers
    assert all(worker.manager_id for worker in workers)


def test_large_task_100_plan_parser_and_fallback_builder() -> None:
    positions = load_large_task_positions()

    assert len(positions) == 100
    assert positions[0].role == "Chief Executive Officer"
    assert positions[-1].role == "Organizational Effectiveness Analyst"

    organization = build_large_task_100_organization(max_agents=12)
    roots = [agent for agent in organization.agents if agent.manager_id is None]

    assert len(organization.agents) == 12
    assert len(roots) == 1
    assert "report_to_human" in roots[0].permissions


def test_large_task_100_governor_can_apply_safe_org_overrides() -> None:
    organization = build_large_task_100_organization(max_agents=12)

    governed, applied, errors = _apply_governor_design_actions(
        organization,
        review={
            "reporting_overrides": [
                {"agent_id": "chief_financial_officer", "manager_id": "chief_technology_officer"},
                {"agent_id": "chief_executive_officer", "manager_id": "chief_operating_officer"},
            ]
        },
        management_models=["openai/gpt-oss-120b:free"],
        worker_models=["poolside/laguna-m.1:free"],
    )

    assert governed.require_agent("chief_financial_officer").manager_id == "chief_executive_officer"
    assert governed.require_agent("chief_executive_officer").manager_id is None
    assert not applied
    assert any("chief_financial_officer" in error for error in errors)
    assert any("refused to move root agent" in error for error in errors)


def test_large_task_100_policy_instantiation_uses_intermediate_managers() -> None:
    organization = _organization_from_design_policy(
        positions=load_large_task_positions(),
        policy={
            "company_name": "OpenForge Test",
            "mission": "Test policy instantiation",
            "manager_role_keywords": ["chief", "head", "lead", "director", "manager", "architect"],
            "default_management_model": "openai/gpt-oss-120b:free",
            "default_worker_model": "poolside/laguna-m.1:free",
        },
        management_models=["openai/gpt-oss-120b:free"],
        worker_models=["poolside/laguna-m.1:free"],
    )
    direct_reports: dict[str, list[str]] = {}
    for agent in organization.agents:
        if agent.manager_id:
            direct_reports.setdefault(agent.manager_id, []).append(agent.id)

    assert len(direct_reports["chief_executive_officer"]) == 4
    assert organization.require_agent("chief_financial_officer").manager_id == "chief_operating_officer"
    assert organization.require_agent("chief_risk_and_governance_officer").manager_id == "chief_executive_officer"
    assert organization.require_agent("security_architect").manager_id == "chief_risk_and_governance_officer"
    assert organization.require_agent("chief_of_staff").manager_id == "chief_operating_officer"
    assert organization.require_agent("portfolio_management_director").manager_id == "chief_operating_officer"
    assert organization.require_agent("customer_interview_researcher").manager_id == "ux_research_lead"
    assert organization.require_agent("competitive_intelligence_analyst").manager_id == "ux_research_lead"
    assert organization.require_agent("financial_controller").manager_id == "chief_financial_officer"
    assert organization.require_agent("budget_and_forecast_analyst").manager_id == "chief_financial_officer"
    assert len(direct_reports["chief_software_architect"]) <= 8
    assert max(len(reports) for reports in direct_reports.values()) <= 8
    assert {agent.worker_type for agent in organization.agents} == {"codex"}
    manager_ids = {agent.manager_id for agent in organization.agents if agent.manager_id}
    for agent in organization.agents:
        if agent.id not in manager_ids:
            assert agent.model != "openai/gpt-oss-120b:free"


def test_large_org_scale_demo_initializes_and_caps_active_slots(tmp_path: Path) -> None:
    db_path = tmp_path / "large.sqlite"
    workspace = tmp_path / "large_workspace"

    output = run_large_org_scale_demo(db_path, workspace, agent_count=120, active_agent_limit=7)

    assert "Workforce Runtime Demo: large-org-scale" in output
    assert "Agents initialized: 120" in output
    assert "Queued worker_run items: 114" in output
    assert "Configured active agent limit: 7" in output
    assert "Claimed active queue items: 7" in output
    assert "Simulated active worker runs: 7" in output
    with WorkforceRuntime(db_path) as runtime:
        agents = runtime.store.list_agents()
        busy_agents = [agent for agent in agents if agent.status == "busy"]
        events = runtime.store.list_events()
        work_items = runtime.store.list_work_items()

    assert len(agents) == 120
    assert len(busy_agents) == 7
    assert len(work_items) == 114
    assert sum(1 for item in work_items if item.status == "leased") == 7
    assert sum(1 for event in events if event.event_type == "worker_run_started") == 7
    assert any(event.event_type == "human_report_registered" for event in events)
