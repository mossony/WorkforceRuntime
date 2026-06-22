from __future__ import annotations

from workforce_runtime.core import AgentProfile
from workforce_runtime.v2.models import Occupancy, Occupant, Organization, WorkerRunRecord
from workforce_runtime.v2.organization import migrate_agents, replace_primary_occupant, validate_organization_state
from workforce_runtime.v2.pipeline import build_demo_state


def test_v2_agent_migration_separates_position_occupant_and_occupancy() -> None:
    agent = AgentProfile(
        id="codex_worker",
        name="Codex Worker",
        role="Runtime Engineer",
        department="Engineering",
        worker_type="codex",
        model="openai/gpt-oss-120b:free",
        responsibilities=["implement runtime"],
        permissions=["read_repo", "run_tests", "report"],
    )
    organization = Organization(
        id="org_v2",
        name="V2 Org",
        mission="Test migration.",
    )

    state = migrate_agents([agent], organization=organization)

    assert set(state.positions) == {"position_codex_worker"}
    assert set(state.occupants) == {"occupant_codex_worker"}
    assert set(state.occupancies) == {"occupancy_codex_worker_primary"}
    assert state.positions["position_codex_worker"].responsibilities == ["implement runtime"]
    assert state.occupants["occupant_codex_worker"].worker_definition_id == "codex"
    assert validate_organization_state(state).ok


def test_v2_replacing_occupant_preserves_position_and_worker_run_history() -> None:
    state, _identity_map = build_demo_state()
    state.worker_runs["run_1"] = WorkerRunRecord(
        id="run_1",
        occupant_id="occupant_runtime",
        position_id="position_runtime_engineer",
        assignment_id="assignment_1",
        task_id="task_1",
        project_id="project_repo_shadow",
        backend="codex",
        status="completed",
    )
    replacement = Occupant(
        id="occupant_runtime_replacement",
        occupant_type="ai_worker",
        display_name="Replacement Runtime Worker",
        worker_definition_id="codex",
        capabilities=["software_engineering"],
    )

    next_state = replace_primary_occupant(
        state,
        position_id="position_runtime_engineer",
        new_occupant=replacement,
        handoff_artifact_id="handoff_1",
    )

    assert next_state.positions["position_runtime_engineer"].title == "Runtime Engineer"
    assert next_state.worker_runs["run_1"].position_id == "position_runtime_engineer"
    assert next_state.worker_runs["run_1"].occupant_id == "occupant_runtime"
    active = next_state.active_primary_occupancy_for("position_runtime_engineer")
    assert active is not None
    assert active.occupant_id == "occupant_runtime_replacement"
    ended = [
        occupancy
        for occupancy in next_state.occupancies.values()
        if occupancy.position_id == "position_runtime_engineer" and occupancy.status == "ended"
    ]
    assert ended
    assert ended[0].handoff_artifact_id == "handoff_1"


def test_v2_invariants_reject_duplicate_primary_occupancy_and_reporting_cycles() -> None:
    state, _identity_map = build_demo_state()
    state.occupancies["duplicate_review_lead"] = Occupancy(
        id="duplicate_review_lead",
        position_id="position_review_lead",
        occupant_id="occupant_runtime",
        occupancy_type="primary",
    )
    state.positions["position_ceo"] = state.positions["position_ceo"].model_copy(
        update={"reports_to_position_id": "position_runtime_engineer"}
    )

    result = validate_organization_state(state)

    assert not result.ok
    assert any("multiple active primary occupancies" in error for error in result.errors)
    assert any("cycle" in error for error in result.errors)
