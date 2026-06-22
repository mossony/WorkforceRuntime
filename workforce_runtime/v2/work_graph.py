from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from workforce_runtime.v2.models import NormalizedEvent, OrganizationState, WorkEdge, WorkGraph, WorkNode


EDGE_BY_EVENT_TYPE = {
    "task_assigned": "assigned_to",
    "review_requested": "requested_review_from",
    "review_completed": "reviewed_by",
    "approval_granted": "approved_by",
    "approval_rejected": "approved_by",
    "task_blocked": "blocked_by",
    "message_sent": "informed",
    "escalation_created": "escalated_to",
    "decision_made": "participated_in_decision",
    "artifact_created": "produced",
    "task_failed": "blocked_by",
}


class WorkGraphBuilder:
    def build(
        self,
        *,
        organization_id: str,
        state: OrganizationState,
        events: list[NormalizedEvent],
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> WorkGraph:
        selected = [
            event
            for event in events
            if event.organization_id == organization_id
            and (window_start is None or event.occurred_at >= window_start)
            and (window_end is None or event.occurred_at <= window_end)
        ]
        graph = WorkGraph(
            organization_id=organization_id,
            observation_window_start=window_start or (min((event.occurred_at for event in selected), default=None)),
            observation_window_end=window_end or (max((event.occurred_at for event in selected), default=None)),
            event_ids=[event.id for event in selected],
        )
        self._add_nodes(graph, state)
        latencies = self._paired_latencies(selected)
        for event in selected:
            edge_type = EDGE_BY_EVENT_TYPE.get(event.event_type)
            if edge_type is None:
                continue
            source_id = self._source_id(event)
            target_id = self._target_id(event)
            if source_id is None or target_id is None or source_id == target_id:
                continue
            self._add_edge(graph, event, source_id, target_id, edge_type, latencies.get(event.id))
        return graph

    def _add_nodes(self, graph: WorkGraph, state: OrganizationState) -> None:
        for position in state.positions.values():
            graph.nodes[position.id] = WorkNode(id=position.id, node_type="position", label=position.title)
        for occupant in state.occupants.values():
            graph.nodes[occupant.id] = WorkNode(id=occupant.id, node_type="occupant", label=occupant.display_name)
        for project in state.projects.values():
            graph.nodes[project.id] = WorkNode(id=project.id, node_type="project", label=project.name)

    def _add_edge(
        self,
        graph: WorkGraph,
        event: NormalizedEvent,
        source_id: str,
        target_id: str,
        edge_type: str,
        latency_seconds: float | None,
    ) -> None:
        key = graph.edge_key(source_id, target_id, edge_type)
        edge = graph.edges.get(key)
        if edge is None:
            edge = WorkEdge(source_id=source_id, target_id=target_id, edge_type=edge_type)
            graph.edges[key] = edge
        edge.count += 1
        edge.first_observed_at = min(edge.first_observed_at, event.occurred_at) if edge.first_observed_at else event.occurred_at
        edge.last_observed_at = max(edge.last_observed_at, event.occurred_at) if edge.last_observed_at else event.occurred_at
        if latency_seconds is not None:
            edge.latencies_seconds.append(latency_seconds)
        if event.event_type in {"task_failed", "approval_rejected"}:
            edge.failure_count += 1
        if event.metadata.get("rework"):
            edge.rework_count += 1
        if event.project_id:
            edge.project_distribution[event.project_id] = edge.project_distribution.get(event.project_id, 0) + 1
        category = str(event.metadata.get("task_category") or "unknown")
        edge.task_category_distribution[category] = edge.task_category_distribution.get(category, 0) + 1
        edge.event_ids.append(event.id)
        graph.nodes.setdefault(source_id, WorkNode(id=source_id, node_type="external_actor", label=source_id))
        graph.nodes.setdefault(target_id, WorkNode(id=target_id, node_type="external_actor", label=target_id))

    def _paired_latencies(self, events: list[NormalizedEvent]) -> dict[str, float]:
        starts: dict[tuple[str, str], NormalizedEvent] = {}
        latencies: dict[str, float] = {}
        for event in sorted(events, key=lambda item: item.occurred_at):
            key = (event.object_type, event.object_id)
            if event.event_type in {"task_created", "review_requested", "approval_requested", "worker_run_started"}:
                starts[key] = event
            if event.event_type in {
                "task_completed",
                "review_completed",
                "approval_granted",
                "approval_rejected",
                "worker_run_completed",
            }:
                start = starts.get(key)
                if start is not None:
                    latencies[event.id] = max((event.occurred_at - start.occurred_at).total_seconds(), 0.0)
        return latencies

    def _source_id(self, event: NormalizedEvent) -> str | None:
        return event.actor_position_id or event.actor_occupant_id or event.metadata.get("external_actor_id")

    def _target_id(self, event: NormalizedEvent) -> str | None:
        if event.target_position_id or event.target_occupant_id:
            return event.target_position_id or event.target_occupant_id
        if event.event_type == "artifact_created" and event.project_id:
            return event.project_id
        return event.project_id


def incoming_edge_counts(graph: WorkGraph) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for edge in graph.edges.values():
        counts[edge.target_id] += edge.count
    return dict(counts)


def outgoing_edge_counts(graph: WorkGraph) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for edge in graph.edges.values():
        counts[edge.source_id] += edge.count
    return dict(counts)
