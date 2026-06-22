from __future__ import annotations

from workforce_runtime.v2.findings import FindingDetector
from workforce_runtime.v2.github_shadow import GitHubShadowConnector
from workforce_runtime.v2.governance import ChangeValidator, RuleBasedGovernor
from workforce_runtime.v2.llm_governor import StructuredLLMGovernor
from workforce_runtime.v2.metrics import MetricsEngine
from workforce_runtime.v2.pipeline import build_demo_github_payloads, build_demo_state
from workforce_runtime.v2.simulation import HistoricalReplaySimulator
from workforce_runtime.v2.snapshot import SnapshotService
from workforce_runtime.v2.store import V2SQLiteStore
from workforce_runtime.v2.work_graph import WorkGraphBuilder


def test_v2_github_shadow_events_are_idempotent_and_replayable(tmp_path) -> None:
    state, identity_map = build_demo_state()
    connector = GitHubShadowConnector()
    events, cursor = connector.ingest_payloads(
        build_demo_github_payloads(),
        organization_id=state.organization.id,
        project_id="project_repo_shadow",
        identity_map=identity_map,
    )

    with V2SQLiteStore(tmp_path / "v2.sqlite") as store:
        assert store.save_events(events) == len(events)
        assert store.save_events(events) == 0
        replayed = store.list_events(state.organization.id)

    assert cursor == str(len(build_demo_github_payloads()))
    assert [event.id for event in replayed] == [event.id for event in sorted(events, key=lambda item: item.occurred_at)]


def test_v2_work_graph_metrics_findings_proposals_and_simulations(tmp_path) -> None:
    state, identity_map = build_demo_state()
    events, _cursor = GitHubShadowConnector().ingest_payloads(
        build_demo_github_payloads(),
        organization_id=state.organization.id,
        project_id="project_repo_shadow",
        identity_map=identity_map,
    )
    graph = WorkGraphBuilder().build(organization_id=state.organization.id, state=state, events=events)
    metrics = MetricsEngine().calculate(state=state, events=events, graph=graph)

    findings = FindingDetector().detect(state=state, graph=graph, metrics=metrics)
    assert any(finding.finding_type == "approval_bottleneck" for finding in findings)
    assert any(metric.name == "median_approval_latency" and metric.value == 45000 for metric in metrics)

    with V2SQLiteStore(tmp_path / "v2.sqlite") as store:
        snapshot = SnapshotService(store).create_snapshot(
            organization_id=state.organization.id,
            state=state,
            reason="test",
            metrics_summary={metric.name: metric.value for metric in metrics},
        )

    governor = RuleBasedGovernor()
    assessment = governor.inspect(snapshot=snapshot, metrics=metrics, findings=findings)
    proposals = governor.propose_changes(assessment=assessment, snapshot=snapshot, findings=findings)
    assert len(proposals) >= 2
    validator = ChangeValidator()
    assert all(validator.validate(state=state, proposal=proposal).ok for proposal in proposals)

    simulations = [HistoricalReplaySimulator().simulate(proposal=proposal, baseline_metrics=metrics) for proposal in proposals]
    assert any(
        simulation.scenario_metric_values["median_approval_latency"]
        < simulation.baseline_metric_values["median_approval_latency"]
        for simulation in simulations
    )


def test_v2_snapshot_structural_diff_and_immutability(tmp_path) -> None:
    state, _identity_map = build_demo_state()
    with V2SQLiteStore(tmp_path / "v2.sqlite") as store:
        service = SnapshotService(store)
        before = service.create_snapshot(organization_id=state.organization.id, state=state, reason="before")
        state.policies["low_risk_approval"] = {"low_risk_auto_approve": True}
        after = service.create_snapshot(organization_id=state.organization.id, state=state, reason="after")

        loaded_before = service.load_snapshot(before.id)
        diff = service.structural_diff(before, after)

    assert loaded_before is not None
    assert "low_risk_approval" not in loaded_before.state.policies
    assert diff["policies_changed"] == ["low_risk_approval"]


def test_v2_llm_governor_retries_and_validates_mock_structured_response(tmp_path) -> None:
    state, identity_map = build_demo_state()
    events, _cursor = GitHubShadowConnector().ingest_payloads(
        build_demo_github_payloads(),
        organization_id=state.organization.id,
        project_id="project_repo_shadow",
        identity_map=identity_map,
    )
    graph = WorkGraphBuilder().build(organization_id=state.organization.id, state=state, events=events)
    metrics = MetricsEngine().calculate(state=state, events=events, graph=graph)
    findings = FindingDetector().detect(state=state, graph=graph, metrics=metrics)
    with V2SQLiteStore(tmp_path / "v2.sqlite") as store:
        snapshot = SnapshotService(store).create_snapshot(
            organization_id=state.organization.id,
            state=state,
            reason="llm-governor-test",
            metrics_summary={metric.name: metric.value for metric in metrics},
        )
    proposals = RuleBasedGovernor().propose_changes(
        assessment={"test": True},
        snapshot=snapshot,
        findings=findings,
    )
    calls = {"count": 0}

    def provider(context: dict) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            bad = proposals[0].model_copy(update={"finding_ids": ["hallucinated_finding"]})
            return {
                "assessment": "bad first attempt",
                "candidate_proposals": [bad.model_dump(mode="json")],
            }
        return {
            "assessment": "approval bottleneck likely has policy and capacity alternatives",
            "root_cause_hypotheses": ["single approver queue"],
            "candidate_proposals": [proposal.model_dump(mode="json") for proposal in proposals[:2]],
            "suggested_evaluation_metrics": ["median_approval_latency", "rejection_rate"],
        }

    response = StructuredLLMGovernor(provider, max_attempts=2).propose_changes(
        snapshot=snapshot,
        metrics=metrics,
        findings=findings,
    )

    assert calls["count"] == 2
    assert response.assessment.startswith("approval bottleneck")
    assert len(response.candidate_proposals) == 2
