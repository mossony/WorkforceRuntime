from __future__ import annotations

from collections import defaultdict
from statistics import median

from workforce_runtime.v2.models import Metric, NormalizedEvent, OrganizationState, WorkGraph
from workforce_runtime.v2.work_graph import incoming_edge_counts


class MetricsEngine:
    definition_version = "v2.0"

    def calculate(
        self,
        *,
        state: OrganizationState,
        events: list[NormalizedEvent],
        graph: WorkGraph,
    ) -> list[Metric]:
        metrics: list[Metric] = []
        organization_id = state.organization.id
        window_start = graph.observation_window_start
        window_end = graph.observation_window_end
        metrics.append(
            Metric(
                name="completed_tasks",
                organization_id=organization_id,
                window_start=window_start,
                window_end=window_end,
                value=sum(1 for event in events if event.event_type == "task_completed"),
                unit="count",
                sample_size=len(events),
                definition_version=self.definition_version,
                evidence_event_ids=[event.id for event in events if event.event_type == "task_completed"],
            )
        )
        metrics.append(
            Metric(
                name="failed_tasks",
                organization_id=organization_id,
                window_start=window_start,
                window_end=window_end,
                value=sum(1 for event in events if event.event_type == "task_failed"),
                unit="count",
                sample_size=len(events),
                definition_version=self.definition_version,
                evidence_event_ids=[event.id for event in events if event.event_type == "task_failed"],
            )
        )
        metrics.extend(self._latency_metrics(organization_id, events, graph))
        metrics.extend(self._quality_metrics(organization_id, events, graph))
        metrics.extend(self._cost_metrics(organization_id, events, graph))
        metrics.extend(self._structure_metrics(state, graph))
        return metrics

    def _latency_metrics(
        self,
        organization_id: str,
        events: list[NormalizedEvent],
        graph: WorkGraph,
    ) -> list[Metric]:
        output: list[Metric] = []
        pairs = self._paired_latencies(events)
        mapping = {
            "task_cycle_time": {"task_completed"},
            "review_latency": {"review_completed", "approval_granted", "approval_rejected"},
            "approval_latency": {"approval_granted", "approval_rejected"},
            "blocked_duration": {"escalation_resolved"},
        }
        for metric_name, event_types in mapping.items():
            values = [latency for event_id, latency in pairs.items() if self._event_type(event_id, events) in event_types]
            output.append(
                Metric(
                    name=f"median_{metric_name}",
                    organization_id=organization_id,
                    window_start=graph.observation_window_start,
                    window_end=graph.observation_window_end,
                    value=float(median(values)) if values else None,
                    unit="seconds",
                    sample_size=len(values),
                    confidence=1.0 if len(values) >= 3 else 0.5 if values else 0.0,
                    missing_reason=None if values else "no paired events",
                    definition_version=self.definition_version,
                    evidence_event_ids=list(pairs),
                )
            )
        return output

    def _quality_metrics(self, organization_id: str, events: list[NormalizedEvent], graph: WorkGraph) -> list[Metric]:
        rejected = [event for event in events if event.event_type == "approval_rejected"]
        approvals = [event for event in events if event.event_type in {"approval_granted", "approval_rejected"}]
        rework = [event for event in events if event.metadata.get("rework")]
        completed = [event for event in events if event.event_type == "task_completed"]
        return [
            Metric(
                name="rejection_rate",
                organization_id=organization_id,
                window_start=graph.observation_window_start,
                window_end=graph.observation_window_end,
                value=len(rejected) / len(approvals) if approvals else None,
                unit="ratio",
                sample_size=len(approvals),
                missing_reason=None if approvals else "no approval events",
                definition_version=self.definition_version,
                evidence_event_ids=[event.id for event in approvals],
            ),
            Metric(
                name="rework_rate",
                organization_id=organization_id,
                window_start=graph.observation_window_start,
                window_end=graph.observation_window_end,
                value=len(rework) / len(completed) if completed else None,
                unit="ratio",
                sample_size=len(completed),
                missing_reason=None if completed else "no completed tasks",
                definition_version=self.definition_version,
                evidence_event_ids=[event.id for event in rework],
            ),
        ]

    def _cost_metrics(self, organization_id: str, events: list[NormalizedEvent], graph: WorkGraph) -> list[Metric]:
        token_events = [event for event in events if event.metadata.get("tokens_used") is not None]
        tokens = sum(float(event.metadata.get("tokens_used") or 0) for event in token_events)
        completed = max(sum(1 for event in events if event.event_type == "task_completed"), 1)
        return [
            Metric(
                name="model_usage_tokens",
                organization_id=organization_id,
                window_start=graph.observation_window_start,
                window_end=graph.observation_window_end,
                value=tokens,
                unit="tokens",
                sample_size=len(token_events),
                confidence=1.0,
                definition_version=self.definition_version,
                evidence_event_ids=[event.id for event in token_events],
            ),
            Metric(
                name="cost_per_accepted_artifact_tokens",
                organization_id=organization_id,
                window_start=graph.observation_window_start,
                window_end=graph.observation_window_end,
                value=tokens / completed,
                unit="tokens",
                sample_size=completed,
                confidence=1.0 if token_events else 0.0,
                missing_reason=None if token_events else "no token usage events",
                definition_version=self.definition_version,
            ),
        ]

    def _structure_metrics(self, state: OrganizationState, graph: WorkGraph) -> list[Metric]:
        organization_id = state.organization.id
        reports: dict[str, int] = defaultdict(int)
        for position in state.positions.values():
            if position.reports_to_position_id:
                reports[position.reports_to_position_id] += 1
        incoming = incoming_edge_counts(graph)
        output: list[Metric] = []
        for position_id in state.positions:
            output.append(
                Metric(
                    name="span_of_control",
                    organization_id=organization_id,
                    position_id=position_id,
                    window_start=graph.observation_window_start,
                    window_end=graph.observation_window_end,
                    value=reports.get(position_id, 0),
                    unit="positions",
                    sample_size=len(state.positions),
                    definition_version=self.definition_version,
                )
            )
            output.append(
                Metric(
                    name="work_centrality",
                    organization_id=organization_id,
                    position_id=position_id,
                    window_start=graph.observation_window_start,
                    window_end=graph.observation_window_end,
                    value=incoming.get(position_id, 0),
                    unit="incoming_edges",
                    sample_size=sum(incoming.values()),
                    definition_version=self.definition_version,
                )
            )
        mismatch = self._authority_work_mismatch(state, graph)
        output.append(
            Metric(
                name="authority_work_mismatch",
                organization_id=organization_id,
                window_start=graph.observation_window_start,
                window_end=graph.observation_window_end,
                value=mismatch,
                unit="ratio",
                sample_size=len(graph.edges),
                definition_version=self.definition_version,
            )
        )
        return output

    def _authority_work_mismatch(self, state: OrganizationState, graph: WorkGraph) -> float:
        formal_edges = {
            (position.id, position.reports_to_position_id)
            for position in state.positions.values()
            if position.reports_to_position_id
        }
        if not graph.edges:
            return 0.0
        bypass = 0
        relevant = 0
        for edge in graph.edges.values():
            if edge.source_id not in state.positions or edge.target_id not in state.positions:
                continue
            relevant += 1
            if (edge.source_id, edge.target_id) not in formal_edges and (edge.target_id, edge.source_id) not in formal_edges:
                bypass += 1
        return bypass / relevant if relevant else 0.0

    def _paired_latencies(self, events: list[NormalizedEvent]) -> dict[str, float]:
        starts: dict[tuple[str, str], NormalizedEvent] = {}
        pairs: dict[str, float] = {}
        for event in sorted(events, key=lambda item: item.occurred_at):
            key = (event.object_type, event.object_id)
            if event.event_type in {"task_created", "review_requested", "approval_requested", "worker_run_started"}:
                starts[key] = event
            elif event.event_type in {
                "task_completed",
                "review_completed",
                "approval_granted",
                "approval_rejected",
                "worker_run_completed",
            }:
                start = starts.get(key)
                if start is not None:
                    pairs[event.id] = max((event.occurred_at - start.occurred_at).total_seconds(), 0.0)
        return pairs

    def _event_type(self, event_id: str, events: list[NormalizedEvent]) -> str | None:
        for event in events:
            if event.id == event_id:
                return event.event_type
        return None
