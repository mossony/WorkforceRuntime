from __future__ import annotations

import pytest
from pydantic import ValidationError

from workforce_runtime.core import (
    AgentProfile,
    Artifact,
    Budget,
    Company,
    Event,
    Organization,
    ReportContract,
    TaskContract,
    UsageCost,
)


def sample_budget() -> Budget:
    return Budget(max_tokens=1000, max_runtime_seconds=300, max_tool_calls=20)


def sample_agent(agent_id: str = "codex_worker", manager_id: str | None = "eng_manager") -> AgentProfile:
    return AgentProfile(
        id=agent_id,
        name="Codex Worker",
        role="Software Engineer",
        department="Engineering",
        manager_id=manager_id,
        worker_type="codex",
        model="openai/gpt-oss-120b:free",
        responsibilities=["Implement assigned engineering tasks"],
        permissions=["read_repo", "run_tests", "submit_artifact", "report"],
        budget=sample_budget(),
    )


def sample_task() -> TaskContract:
    return TaskContract(
        task_id="task_001",
        title="Fix parser test",
        objective="Fix the failing parser test and provide evidence.",
        assigned_to="codex_worker",
        assigned_by="eng_manager",
        root_goal_id="goal_001",
        context_refs=["repo://sample"],
        constraints=["Do not change public API"],
        acceptance_criteria=["pytest passes"],
        budget=sample_budget(),
        risk_level="medium",
        required_artifacts=["git_diff", "test_log"],
        status="assigned",
    )


def test_agent_profile_json_round_trip() -> None:
    agent = sample_agent()

    serialized = agent.model_dump_json()
    restored = AgentProfile.model_validate_json(serialized)

    assert restored == agent
    assert restored.model == "openai/gpt-oss-120b:free"
    assert restored.has_permission("run_tests")
    assert not restored.has_permission("hire_agent")


def test_task_contract_json_round_trip() -> None:
    task = sample_task()

    serialized = task.model_dump_json()
    restored = TaskContract.model_validate_json(serialized)

    assert restored == task


def test_report_contract_json_round_trip() -> None:
    report = ReportContract(
        report_id="report_001",
        from_agent_id="codex_worker",
        to_agent_id="eng_manager",
        task_id="task_001",
        summary="Fixed the parser test.",
        status="completed",
        work_done=["Inspected failure", "Patched parser", "Ran pytest"],
        evidence=[{"type": "test_log", "path": "artifacts/task_001/pytest.log"}],
        risks=[],
        blockers=[],
        confidence=0.9,
        cost=UsageCost(tokens_used=800, runtime_seconds=120, tool_calls=6),
        next_action="Ready for review.",
        requires_decision=False,
        alignment_check="Matches acceptance criteria.",
    )

    serialized = report.model_dump_json()
    restored = ReportContract.model_validate_json(serialized)

    assert restored == report


def test_artifact_and_event_json_round_trip() -> None:
    artifact = Artifact(
        artifact_id="artifact_001",
        task_id="task_001",
        agent_id="codex_worker",
        type="git_diff",
        path="artifacts/task_001/diff.patch",
        description="Patch produced by Codex.",
    )
    event = Event(
        event_id="event_001",
        event_type="task_assigned",
        actor_id="eng_manager",
        task_id="task_001",
        payload={"assigned_to": "codex_worker"},
    )

    assert Artifact.model_validate_json(artifact.model_dump_json()) == artifact
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_rejects_invalid_task_status() -> None:
    with pytest.raises(ValidationError):
        TaskContract(
            task_id="task_001",
            title="Invalid status task",
            objective="Show validation works.",
            status="waiting",
        )


def test_budget_usage_updates() -> None:
    budget = sample_budget()

    budget.record_usage(tokens=250, runtime_seconds=30, tool_calls=3)

    assert budget.tokens_used == 250
    assert budget.runtime_seconds_used == 30
    assert budget.tool_calls_used == 3
    assert not budget.would_exceed(tokens=100, runtime_seconds=10, tool_calls=1)
    assert budget.would_exceed(tokens=800)

    with pytest.raises(ValueError):
        budget.record_usage(tokens=-1)


def test_organization_manager_and_direct_report_relationships() -> None:
    ceo = AgentProfile(
        id="ceo",
        name="CEO Agent",
        role="CEO",
        department="Executive",
        manager_id=None,
        worker_type="generic_cli",
        permissions=["delegate_task", "approve_budget", "hire_agent"],
        budget=Budget(max_tokens=100000, max_runtime_seconds=7200, max_tool_calls=100),
    )
    manager = AgentProfile(
        id="eng_manager",
        name="Engineering Manager Agent",
        role="Engineering Manager",
        department="Engineering",
        manager_id="ceo",
        worker_type="generic_cli",
        permissions=["delegate_task", "request_budget", "report"],
        budget=Budget(max_tokens=50000, max_runtime_seconds=3600, max_tool_calls=80),
    )
    worker = sample_agent()
    organization = Organization(
        company=Company(name="Demo Workforce", mission="Build software using AI workers."),
        agents=[ceo, manager, worker],
    )

    assert organization.find_agent("codex_worker") == worker
    assert organization.get_manager("codex_worker") == manager
    assert organization.get_direct_reports("ceo") == [manager]
    assert organization.get_direct_reports("eng_manager") == [worker]
    assert organization.get_reporting_chain("codex_worker") == [manager, ceo]
    assert organization.get_department_agents("Engineering") == [manager, worker]


def test_organization_rejects_missing_manager() -> None:
    with pytest.raises(ValidationError):
        Organization(
            company=Company(name="Broken Org"),
            agents=[sample_agent(manager_id="missing_manager")],
        )
