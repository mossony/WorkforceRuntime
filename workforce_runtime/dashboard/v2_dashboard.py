from __future__ import annotations

from workforce_runtime.v2.pipeline import V2ShadowRunResult
from workforce_runtime.v2.store import V2SQLiteStore


def render_v2_shadow_dashboard(result: V2ShadowRunResult) -> str:
    lines = [
        "Workforce Runtime V2 Shadow Dashboard",
        "",
        "Organization",
        f"- id: {result.organization_id}",
        f"- imported events: {result.imported_event_count}",
        f"- positions: {len(result.baseline_snapshot.state.positions)}",
        f"- occupants: {len(result.baseline_snapshot.state.occupants)}",
        "",
        "Authority vs Work",
        f"- authority positions: {len(result.baseline_snapshot.state.positions)}",
        f"- observed work edges: {len(result.work_graph.edges)}",
        f"- mismatch: {_metric_value(result, 'authority_work_mismatch')}",
        "",
        "Findings",
    ]
    if result.findings:
        for finding in result.findings:
            lines.append(f"- {finding.finding_type} severity={finding.severity} confidence={finding.confidence:.2f}")
    else:
        lines.append("- none")
    lines.extend(["", "Proposals"])
    for proposal in result.proposals:
        lines.append(f"- {proposal.id} status={proposal.status} risk={proposal.risk_level}")
    lines.extend(["", "Simulations"])
    for simulation in result.simulations:
        label = simulation.proposal_id or "baseline"
        lines.append(f"- {label}: median_approval_latency={simulation.scenario_metric_values.get('median_approval_latency')}")
    lines.extend(
        [
            "",
            "Decisions",
            f"- {result.decision.id} selected={result.decision.selected_option_id} status={result.decision.status}",
            "",
            "Experiments",
            f"- {result.experiment.id} conclusion={result.experiment.conclusion} observed={result.experiment.observed_effect}",
            "",
            "Audit Trail",
        ]
    )
    for audit_id in result.audit_record_ids:
        lines.append(f"- {audit_id}")
    lines.extend(
        [
            "",
            "Full Chain",
            "event -> metric -> finding -> proposal -> simulation -> decision -> applied change -> experiment result",
        ]
    )
    return "\n".join(lines)


def render_v2_store_summary(store: V2SQLiteStore, organization_id: str) -> str:
    events = store.list_events(organization_id)
    snapshots = store.list_snapshots(organization_id)
    findings = store.list_findings(organization_id)
    proposals = store.list_proposals()
    decisions = store.list_decisions()
    experiments = store.list_experiments()
    audit_records = store.list_audit_records(organization_id)
    lines = [
        "Workforce Runtime V2 Store Summary",
        f"- events: {len(events)}",
        f"- snapshots: {len(snapshots)}",
        f"- findings: {len(findings)}",
        f"- proposals: {len(proposals)}",
        f"- decisions: {len(decisions)}",
        f"- experiments: {len(experiments)}",
        f"- audit records: {len(audit_records)}",
    ]
    return "\n".join(lines)


def _metric_value(result: V2ShadowRunResult, name: str) -> str:
    for metric in result.metrics:
        if metric.name == name:
            return str(metric.value)
    return "missing"
