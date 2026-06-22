from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.v2.change_management import ChangeManager
from workforce_runtime.v2.decision import DecisionLedger
from workforce_runtime.v2.experiment import ExperimentRunner
from workforce_runtime.v2.findings import FindingDetector
from workforce_runtime.v2.github_shadow import GitHubShadowConnector
from workforce_runtime.v2.governance import ChangeValidator, RuleBasedGovernor
from workforce_runtime.v2.metrics import MetricsEngine
from workforce_runtime.v2.models import (
    Decision,
    Department,
    Experiment,
    Finding,
    Metric,
    NormalizedEvent,
    Occupancy,
    Occupant,
    Organization,
    OrganizationChangeProposal,
    OrganizationSnapshot,
    OrganizationState,
    Position,
    Project,
    SimulationResult,
    WorkGraph,
)
from workforce_runtime.v2.snapshot import SnapshotService
from workforce_runtime.v2.store import V2SQLiteStore
from workforce_runtime.v2.work_graph import WorkGraphBuilder


class V2ShadowRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    imported_event_count: int
    work_graph: WorkGraph
    metrics: list[Metric]
    findings: list[Finding]
    proposals: list[OrganizationChangeProposal]
    simulations: list[SimulationResult]
    decision: Decision
    selected_proposal_id: str
    baseline_snapshot: OrganizationSnapshot
    post_change_snapshot: OrganizationSnapshot
    experiment: Experiment
    audit_record_ids: list[str] = Field(default_factory=list)
    treatment_events: list[NormalizedEvent] = Field(default_factory=list)


def build_demo_state() -> tuple[OrganizationState, dict[str, dict[str, str]]]:
    organization = Organization(
        id="workforce_v2_demo",
        name="Workforce Runtime V2 Demo Org",
        mission="Observe repository work and improve organizational flow.",
        root_goal_ids=["goal_shadow_governance"],
    )
    departments = {
        "executive": Department(
            id="executive",
            organization_id=organization.id,
            name="Executive",
            leader_position_id="position_ceo",
            mandate=["approve structural changes"],
        ),
        "engineering": Department(
            id="engineering",
            organization_id=organization.id,
            name="Engineering",
            leader_position_id="position_vp_engineering",
            mandate=["ship runtime changes", "maintain review quality"],
        ),
    }
    positions = {
        "position_ceo": Position(
            id="position_ceo",
            organization_id=organization.id,
            department_id="executive",
            title="CEO",
            responsibilities=["approve high-risk governance"],
            required_capabilities=["governance"],
        ),
        "position_vp_engineering": Position(
            id="position_vp_engineering",
            organization_id=organization.id,
            department_id="engineering",
            title="VP Engineering",
            reports_to_position_id="position_ceo",
            responsibilities=["engineering delivery", "review policy"],
            required_capabilities=["management", "code_review"],
        ),
        "position_runtime_engineer": Position(
            id="position_runtime_engineer",
            organization_id=organization.id,
            department_id="engineering",
            title="Runtime Engineer",
            reports_to_position_id="position_vp_engineering",
            responsibilities=["runtime implementation"],
            required_capabilities=["software_engineering", "test_execution"],
        ),
        "position_review_lead": Position(
            id="position_review_lead",
            organization_id=organization.id,
            department_id="engineering",
            title="Review Lead",
            reports_to_position_id="position_vp_engineering",
            responsibilities=["approve pull requests", "review risk"],
            required_capabilities=["code_review", "approval"],
        ),
        "position_ci_triage": Position(
            id="position_ci_triage",
            organization_id=organization.id,
            department_id="engineering",
            title="CI Triage",
            reports_to_position_id="position_vp_engineering",
            responsibilities=["diagnose CI failures"],
            required_capabilities=["ci_debugging", "test_execution"],
        ),
    }
    occupants = {
        "occupant_ceo": Occupant(id="occupant_ceo", occupant_type="human", display_name="Human CEO", capabilities=["governance"]),
        "occupant_vp": Occupant(id="occupant_vp", occupant_type="human", display_name="Engineering VP", capabilities=["management", "code_review"]),
        "occupant_runtime": Occupant(
            id="occupant_runtime",
            occupant_type="human",
            display_name="Runtime Engineer",
            capabilities=["software_engineering", "test_execution"],
        ),
        "occupant_review_lead": Occupant(
            id="occupant_review_lead",
            occupant_type="human",
            display_name="Review Lead",
            capabilities=["code_review", "approval"],
        ),
        "occupant_codex_ci": Occupant(
            id="occupant_codex_ci",
            occupant_type="ai_worker",
            display_name="Codex CI Triage Worker",
            worker_definition_id="codex",
            capabilities=["ci_debugging", "test_execution"],
        ),
    }
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    occupancies = {
        "occupancy_ceo": Occupancy(id="occupancy_ceo", position_id="position_ceo", occupant_id="occupant_ceo", effective_from=now),
        "occupancy_vp": Occupancy(id="occupancy_vp", position_id="position_vp_engineering", occupant_id="occupant_vp", effective_from=now),
        "occupancy_runtime": Occupancy(
            id="occupancy_runtime",
            position_id="position_runtime_engineer",
            occupant_id="occupant_runtime",
            effective_from=now,
        ),
        "occupancy_review_lead": Occupancy(
            id="occupancy_review_lead",
            position_id="position_review_lead",
            occupant_id="occupant_review_lead",
            effective_from=now,
        ),
        "occupancy_ci_triage": Occupancy(
            id="occupancy_ci_triage",
            position_id="position_ci_triage",
            occupant_id="occupant_codex_ci",
            effective_from=now,
        ),
    }
    projects = {
        "project_repo_shadow": Project(
            id="project_repo_shadow",
            organization_id=organization.id,
            name="Repository Shadow Governance",
            owner_position_id="position_vp_engineering",
            root_goal_id="goal_shadow_governance",
            success_metrics=["median_approval_latency", "human_interventions", "rejection_rate"],
        )
    }
    state = OrganizationState(
        organization=organization,
        departments=departments,
        positions=positions,
        occupants=occupants,
        occupancies=occupancies,
        projects=projects,
        policies={"allow_multi_position_occupants": False},
    )
    identity_map = {
        "alice": {"position_id": "position_runtime_engineer", "occupant_id": "occupant_runtime"},
        "reviewlead": {"position_id": "position_review_lead", "occupant_id": "occupant_review_lead"},
        "vp": {"position_id": "position_vp_engineering", "occupant_id": "occupant_vp"},
        "codex-ci": {"position_id": "position_ci_triage", "occupant_id": "occupant_codex_ci"},
    }
    return state, identity_map


def build_demo_github_payloads() -> list[dict[str, Any]]:
    base = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    payloads: list[dict[str, Any]] = []
    for index in range(6):
        created = base + timedelta(days=index)
        approved = created + timedelta(hours=10 + index)
        pr_id = 1000 + index
        payloads.append(
            {
                "event": "pull_request",
                "action": "opened",
                "id": f"pr-open-{pr_id}",
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "sender": {"login": "alice"},
                "pull_request": {
                    "id": pr_id,
                    "number": index + 1,
                    "title": f"Runtime change {index + 1}",
                    "state": "open",
                    "user": {"login": "alice"},
                    "created_at": created.isoformat().replace("+00:00", "Z"),
                },
            }
        )
        payloads.append(
            {
                "event": "pull_request_review",
                "action": "submitted",
                "id": f"review-{pr_id}",
                "created_at": approved.isoformat().replace("+00:00", "Z"),
                "sender": {"login": "reviewlead"},
                "pull_request": {
                    "id": pr_id,
                    "number": index + 1,
                    "user": {"login": "alice"},
                },
                "review": {
                    "id": f"review-object-{pr_id}",
                    "state": "approved",
                    "submitted_at": approved.isoformat().replace("+00:00", "Z"),
                },
            }
        )
    return payloads


def run_v2_shadow_demo(
    *,
    db_path: Path,
    github_payloads: list[dict[str, Any]] | None = None,
    github_events_path: Path | None = None,
) -> V2ShadowRunResult:
    state, identity_map = build_demo_state()
    connector = GitHubShadowConnector()
    if github_events_path is not None:
        payloads = connector.load_payloads(github_events_path)
    else:
        payloads = github_payloads or build_demo_github_payloads()

    with V2SQLiteStore(db_path) as store:
        store.save_state(state)
        events, cursor = connector.ingest_payloads(
            payloads,
            organization_id=state.organization.id,
            project_id="project_repo_shadow",
            identity_map=identity_map,
        )
        store.save_events(events)
        baseline_events = sorted(events, key=lambda item: item.occurred_at)

        graph = WorkGraphBuilder().build(organization_id=state.organization.id, state=state, events=baseline_events)
        store.save_work_graph(graph)

        metrics = MetricsEngine().calculate(state=state, events=baseline_events, graph=graph)
        snapshot_service = SnapshotService(store)
        baseline_snapshot = snapshot_service.create_snapshot(
            organization_id=state.organization.id,
            state=state,
            reason="v2_baseline_shadow_observation",
            metrics_summary={metric.name: metric.value for metric in metrics},
            source_event_cursor=cursor,
        )

        findings = FindingDetector().detect(state=state, graph=graph, metrics=metrics)
        for finding in findings:
            store.save_finding(finding)

        governor = RuleBasedGovernor()
        assessment = governor.inspect(snapshot=baseline_snapshot, metrics=metrics, findings=findings)
        proposals = governor.propose_changes(assessment=assessment, snapshot=baseline_snapshot, findings=findings)
        validator = ChangeValidator()
        validated_proposals: list[OrganizationChangeProposal] = []
        for proposal in proposals:
            validation = validator.validate(state=state, proposal=proposal)
            next_status = "validated" if validation.ok else "proposed"
            updated = proposal.model_copy(update={"status": next_status, "validation_errors": validation.errors})
            store.save_proposal(updated)
            validated_proposals.append(updated)

        simulator = HistoricalReplaySimulator()
        simulations = [simulator.baseline(metrics=metrics)]
        simulations.extend(
            simulator.simulate(proposal=proposal, baseline_metrics=metrics)
            for proposal in validated_proposals
            if proposal.status == "validated"
        )
        for simulation in simulations:
            store.save_simulation_result(simulation)

        decision_ledger = DecisionLedger()
        decision = decision_ledger.create_for_proposals(
            decision_id="decision_v2_shadow_intervention",
            organization_id=state.organization.id,
            project_id="project_repo_shadow",
            owner_position_id="position_vp_engineering",
            question="Which organizational change should be applied to reduce repository approval latency?",
            proposals=validated_proposals,
            simulations=simulations,
        )
        selected_proposal = _select_best_proposal(validated_proposals, simulations)
        decision = decision_ledger.select_option(
            decision,
            option_id=f"option_{selected_proposal.id}",
            rationale=["Selected the proposal with the best simulated approval-latency reduction and acceptable guardrails."],
        )
        store.save_decision(decision)

        change_manager = ChangeManager(store=store, snapshot_service=snapshot_service)
        approved, _approval = change_manager.approve(
            proposal=selected_proposal,
            approver_position_id="position_vp_engineering",
            decision="approved",
            rationale="Sandbox approval for V2 demo.",
        )
        changed_state, audit_records = change_manager.apply(
            state=state,
            proposal=approved,
            approver_position_id="position_vp_engineering",
        )
        post_change_snapshot = store.list_snapshots(state.organization.id)[-1]

        treatment_events = _build_treatment_events(state.organization.id)
        store.save_events(treatment_events)
        combined_treatment_events = treatment_events
        treatment_graph = WorkGraphBuilder().build(
            organization_id=state.organization.id,
            state=changed_state,
            events=combined_treatment_events,
        )
        treatment_metrics = MetricsEngine().calculate(
            state=changed_state,
            events=combined_treatment_events,
            graph=treatment_graph,
        )

        experiment = ExperimentRunner().create_experiment(
            experiment_id="experiment_v2_shadow_intervention",
            baseline_snapshot=baseline_snapshot,
            applied_proposal=approved,
            target_metrics=["median_approval_latency"],
            guardrail_metrics=["rejection_rate"],
            expected_effect={"median_approval_latency": -0.25},
            rollback_thresholds={"rejection_rate": 0.05},
        )
        experiment = ExperimentRunner().evaluate(
            experiment=experiment,
            baseline_metrics=metrics,
            treatment_metrics=treatment_metrics,
        )
        store.save_experiment(experiment)
        return V2ShadowRunResult(
            organization_id=state.organization.id,
            imported_event_count=len(baseline_events),
            work_graph=graph,
            metrics=metrics,
            findings=findings,
            proposals=validated_proposals,
            simulations=simulations,
            decision=decision,
            selected_proposal_id=selected_proposal.id,
            baseline_snapshot=baseline_snapshot,
            post_change_snapshot=post_change_snapshot,
            experiment=experiment,
            audit_record_ids=[record.id for record in audit_records],
            treatment_events=treatment_events,
        )


def _select_best_proposal(
    proposals: list[OrganizationChangeProposal],
    simulations: list[SimulationResult],
) -> OrganizationChangeProposal:
    by_proposal = {simulation.proposal_id: simulation for simulation in simulations if simulation.proposal_id}
    valid = [proposal for proposal in proposals if proposal.status == "validated"]
    if not valid:
        raise RuntimeError("no valid V2 proposal generated")

    def score(proposal: OrganizationChangeProposal) -> float:
        simulation = by_proposal.get(proposal.id)
        if simulation is None:
            return float("inf")
        value = simulation.scenario_metric_values.get("median_approval_latency")
        return float(value) if isinstance(value, (int, float)) else float("inf")

    return sorted(valid, key=score)[0]


def _build_treatment_events(organization_id: str) -> list[NormalizedEvent]:
    base = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    events: list[NormalizedEvent] = []
    for index in range(6):
        created = base + timedelta(days=index)
        approved = created + timedelta(hours=3)
        pr_id = f"treatment_pr_{index + 1}"
        events.append(
            NormalizedEvent(
                id=f"treatment_task_created_{index}",
                organization_id=organization_id,
                project_id="project_repo_shadow",
                actor_position_id="position_runtime_engineer",
                actor_occupant_id="occupant_runtime",
                event_type="task_created",
                object_type="pull_request",
                object_id=pr_id,
                occurred_at=created,
                source="controlled_workload",
                source_event_id=f"controlled_created_{index}",
                task_id=pr_id,
                metadata={"backend": "codex", "task_category": "code_review", "tokens_used": 800},
            )
        )
        events.append(
            NormalizedEvent(
                id=f"treatment_worker_completed_{index}",
                organization_id=organization_id,
                project_id="project_repo_shadow",
                actor_position_id="position_ci_triage",
                actor_occupant_id="occupant_codex_ci",
                target_position_id="position_runtime_engineer",
                target_occupant_id="occupant_runtime",
                event_type="approval_granted",
                object_type="pull_request",
                object_id=pr_id,
                occurred_at=approved,
                source="controlled_workload",
                source_event_id=f"controlled_approved_{index}",
                task_id=pr_id,
                metadata={"backend": "codex", "task_category": "code_review", "tokens_used": 1200},
            )
        )
        events.append(
            NormalizedEvent(
                id=f"treatment_task_completed_{index}",
                organization_id=organization_id,
                project_id="project_repo_shadow",
                actor_position_id="position_runtime_engineer",
                actor_occupant_id="occupant_runtime",
                event_type="task_completed",
                object_type="pull_request",
                object_id=pr_id,
                occurred_at=approved + timedelta(minutes=15),
                source="controlled_workload",
                source_event_id=f"controlled_completed_{index}",
                task_id=pr_id,
                metadata={"backend": "codex", "task_category": "code_review"},
            )
        )
    return events


from workforce_runtime.v2.simulation import HistoricalReplaySimulator
