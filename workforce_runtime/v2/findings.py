from __future__ import annotations

from workforce_runtime.v2.models import Finding, Metric, OrganizationState, WorkGraph


def metric_lookup(metrics: list[Metric]) -> dict[tuple[str, str | None], Metric]:
    return {(metric.name, metric.position_id): metric for metric in metrics}


class FindingDetector:
    def detect(
        self,
        *,
        state: OrganizationState,
        graph: WorkGraph,
        metrics: list[Metric],
    ) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self.detect_approval_bottlenecks(state=state, graph=graph, metrics=metrics))
        findings.extend(self.detect_overloaded_positions(state=state, graph=graph, metrics=metrics))
        findings.extend(self.detect_hidden_dependencies(state=state, graph=graph, metrics=metrics))
        findings.extend(self.detect_authority_work_mismatch(state=state, graph=graph, metrics=metrics))
        return self._dedupe(findings)

    def detect_approval_bottlenecks(
        self,
        *,
        state: OrganizationState,
        graph: WorkGraph,
        metrics: list[Metric],
    ) -> list[Finding]:
        latency = next((metric for metric in metrics if metric.name == "median_approval_latency"), None)
        rejection = next((metric for metric in metrics if metric.name == "rejection_rate"), None)
        approval_edges = [edge for edge in graph.edges.values() if edge.edge_type == "approved_by"]
        total_approvals = sum(edge.count for edge in approval_edges)
        findings: list[Finding] = []
        for edge in approval_edges:
            share = edge.count / total_approvals if total_approvals else 0.0
            slow = latency is not None and latency.value is not None and float(latency.value) >= 6 * 60 * 60
            concentrated = share >= 0.4
            low_rejection = rejection is None or rejection.value is None or float(rejection.value) <= 0.05
            if slow and concentrated and low_rejection:
                findings.append(
                    Finding(
                        id=f"finding_approval_bottleneck_{edge.source_id}",
                        organization_id=state.organization.id,
                        finding_type="approval_bottleneck",
                        severity="high",
                        confidence=0.9,
                        affected_positions=[edge.source_id],
                        affected_projects=list(edge.project_distribution),
                        supporting_metric_names=["median_approval_latency", "rejection_rate"],
                        supporting_event_ids=edge.event_ids,
                        suggested_investigation="Check whether low-risk approvals can be routed away from this position.",
                        metadata={"approval_share": share},
                    )
                )
        return findings

    def detect_overloaded_positions(
        self,
        *,
        state: OrganizationState,
        graph: WorkGraph,
        metrics: list[Metric],
    ) -> list[Finding]:
        lookup = metric_lookup(metrics)
        findings: list[Finding] = []
        for position_id in state.positions:
            centrality = lookup.get(("work_centrality", position_id))
            span = lookup.get(("span_of_control", position_id))
            active_tasks = sum(
                1
                for run in state.worker_runs.values()
                if run.position_id == position_id and run.status in {"queued", "running"}
            )
            centrality_value = float(centrality.value or 0) if centrality else 0.0
            span_value = float(span.value or 0) if span else 0.0
            if active_tasks >= 3 or centrality_value >= 5 or span_value >= 6:
                findings.append(
                    Finding(
                        id=f"finding_overloaded_{position_id}",
                        organization_id=state.organization.id,
                        finding_type="overloaded_position",
                        severity="high" if active_tasks >= 5 or centrality_value >= 8 else "medium",
                        confidence=0.82,
                        affected_positions=[position_id],
                        supporting_metric_names=["work_centrality", "span_of_control"],
                        suggested_investigation="Split incoming queue or add a backup/delegate position.",
                        metadata={"active_tasks": active_tasks, "work_centrality": centrality_value, "span_of_control": span_value},
                    )
                )
        return findings

    def detect_hidden_dependencies(
        self,
        *,
        state: OrganizationState,
        graph: WorkGraph,
        metrics: list[Metric],
    ) -> list[Finding]:
        lookup = metric_lookup(metrics)
        findings: list[Finding] = []
        for position_id, position in state.positions.items():
            centrality = lookup.get(("work_centrality", position_id))
            span = lookup.get(("span_of_control", position_id))
            centrality_value = float(centrality.value or 0) if centrality else 0.0
            formal_authority = float(span.value or 0) if span else 0.0
            has_backup = any(
                occupancy.position_id == position_id
                and occupancy.occupancy_type == "backup"
                and occupancy.status == "active"
                for occupancy in state.occupancies.values()
            )
            if centrality_value >= 4 and formal_authority == 0 and not has_backup and position.status == "active":
                findings.append(
                    Finding(
                        id=f"finding_hidden_dependency_{position_id}",
                        organization_id=state.organization.id,
                        finding_type="hidden_dependency",
                        severity="medium",
                        confidence=0.78,
                        affected_positions=[position_id],
                        supporting_metric_names=["work_centrality", "span_of_control"],
                        suggested_investigation="Create a backup or formalize this position's dependency role.",
                        metadata={"work_centrality": centrality_value},
                    )
                )
        return findings

    def detect_authority_work_mismatch(
        self,
        *,
        state: OrganizationState,
        graph: WorkGraph,
        metrics: list[Metric],
    ) -> list[Finding]:
        mismatch = next((metric for metric in metrics if metric.name == "authority_work_mismatch"), None)
        if mismatch is None or mismatch.value is None or float(mismatch.value) < 0.35:
            return []
        event_ids: list[str] = []
        affected: set[str] = set()
        for edge in graph.edges.values():
            if edge.source_id in state.positions:
                affected.add(edge.source_id)
            if edge.target_id in state.positions:
                affected.add(edge.target_id)
            event_ids.extend(edge.event_ids[:2])
        return [
            Finding(
                id="finding_authority_work_mismatch",
                organization_id=state.organization.id,
                finding_type="authority_work_mismatch",
                severity="medium",
                confidence=0.74,
                affected_positions=sorted(affected),
                supporting_metric_names=["authority_work_mismatch"],
                supporting_event_ids=event_ids[:10],
                suggested_investigation="Compare formal reporting lines with actual review and approval paths.",
                metadata={"mismatch_ratio": float(mismatch.value)},
            )
        ]

    def _dedupe(self, findings: list[Finding]) -> list[Finding]:
        by_id: dict[str, Finding] = {}
        for finding in findings:
            by_id[finding.id] = finding
        return list(by_id.values())
