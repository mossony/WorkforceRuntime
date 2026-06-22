from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


PositionStatus = Literal["active", "vacant", "archived"]
OccupantStatus = Literal["available", "busy", "suspended", "archived"]
OccupancyStatus = Literal["active", "ended"]
OccupancyType = Literal["primary", "acting", "backup", "delegate", "mirror"]
WorkerRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
FindingStatus = Literal["open", "dismissed", "resolved"]
ProposalStatus = Literal[
    "draft",
    "proposed",
    "validated",
    "simulated",
    "awaiting_approval",
    "approved",
    "rejected",
    "applying",
    "active",
    "evaluating",
    "retained",
    "rolled_back",
]
DecisionStatus = Literal[
    "draft",
    "collecting_evidence",
    "awaiting_decision",
    "decided",
    "active",
    "evaluating",
    "validated",
    "invalidated",
    "superseded",
]
ExperimentStatus = Literal[
    "planned",
    "baseline_collection",
    "active",
    "evaluating",
    "retained",
    "rolled_back",
    "inconclusive",
]


class V2BaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Organization(V2BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mission: str = Field(min_length=1)
    status: Literal["active", "archived"] = "active"
    root_goal_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Department(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    parent_department_id: str | None = None
    leader_position_id: str | None = None
    mandate: list[str] = Field(default_factory=list)


class Position(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    department_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    reports_to_position_id: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    authority_policy_ids: list[str] = Field(default_factory=list)
    budget_account_id: str | None = None
    status: PositionStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Occupant(V2BaseModel):
    id: str = Field(min_length=1)
    occupant_type: Literal["human", "ai_worker", "team", "service", "external_contractor"]
    display_name: str = Field(min_length=1)
    worker_definition_id: str | None = None
    human_identity_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    status: OccupantStatus = "available"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Occupancy(V2BaseModel):
    id: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    occupant_id: str = Field(min_length=1)
    occupancy_type: OccupancyType = "primary"
    effective_from: datetime = Field(default_factory=utc_now)
    effective_to: datetime | None = None
    status: OccupancyStatus = "active"
    handoff_artifact_id: str | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> Occupancy:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class WorkerRunRecord(V2BaseModel):
    id: str = Field(min_length=1)
    occupant_id: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    status: WorkerRunStatus = "queued"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    report_id: str | None = None


class Project(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    owner_position_id: str = Field(min_length=1)
    root_goal_id: str | None = None
    status: Literal["active", "paused", "completed", "archived"] = "active"
    budget_account_id: str | None = None
    success_metrics: list[str] = Field(default_factory=list)


class Goal(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    parent_goal_id: str | None = None
    objective: str = Field(min_length=1)
    owner_position_id: str = Field(min_length=1)
    success_criteria: list[str] = Field(default_factory=list)
    status: Literal["active", "completed", "archived"] = "active"


class OrganizationState(V2BaseModel):
    organization: Organization
    departments: dict[str, Department] = Field(default_factory=dict)
    positions: dict[str, Position] = Field(default_factory=dict)
    occupants: dict[str, Occupant] = Field(default_factory=dict)
    occupancies: dict[str, Occupancy] = Field(default_factory=dict)
    projects: dict[str, Project] = Field(default_factory=dict)
    goals: dict[str, Goal] = Field(default_factory=dict)
    worker_runs: dict[str, WorkerRunRecord] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def active_primary_occupancy_for(self, position_id: str) -> Occupancy | None:
        for occupancy in self.occupancies.values():
            if (
                occupancy.position_id == position_id
                and occupancy.occupancy_type == "primary"
                and occupancy.status == "active"
            ):
                return occupancy
        return None

    def manager_chain(self, position_id: str) -> list[str]:
        chain: list[str] = []
        current = self.positions.get(position_id)
        seen: set[str] = set()
        while current and current.reports_to_position_id:
            manager_id = current.reports_to_position_id
            if manager_id in seen:
                chain.append(manager_id)
                break
            chain.append(manager_id)
            seen.add(manager_id)
            current = self.positions.get(manager_id)
        return chain


class NormalizedEvent(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    project_id: str | None = None
    actor_position_id: str | None = None
    actor_occupant_id: str | None = None
    target_position_id: str | None = None
    target_occupant_id: str | None = None
    event_type: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    observed_at: datetime = Field(default_factory=utc_now)
    source: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    task_id: str | None = None
    raw_payload_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def idempotency_key(self) -> tuple[str, str]:
        return self.source, self.source_event_id


class WorkNode(V2BaseModel):
    id: str = Field(min_length=1)
    node_type: Literal["position", "occupant", "project", "artifact", "decision", "external_actor"]
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkEdge(V2BaseModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    edge_type: str = Field(min_length=1)
    count: int = 0
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    latencies_seconds: list[float] = Field(default_factory=list)
    failure_count: int = 0
    rework_count: int = 0
    project_distribution: dict[str, int] = Field(default_factory=dict)
    task_category_distribution: dict[str, int] = Field(default_factory=dict)
    event_ids: list[str] = Field(default_factory=list)

    @property
    def median_latency_seconds(self) -> float | None:
        if not self.latencies_seconds:
            return None
        return float(median(self.latencies_seconds))

    @property
    def mean_latency_seconds(self) -> float | None:
        if not self.latencies_seconds:
            return None
        return float(mean(self.latencies_seconds))

    @property
    def failure_rate(self) -> float:
        if self.count == 0:
            return 0.0
        return self.failure_count / self.count

    @property
    def rework_rate(self) -> float:
        if self.count == 0:
            return 0.0
        return self.rework_count / self.count


class WorkGraph(V2BaseModel):
    organization_id: str = Field(min_length=1)
    nodes: dict[str, WorkNode] = Field(default_factory=dict)
    edges: dict[str, WorkEdge] = Field(default_factory=dict)
    observation_window_start: datetime | None = None
    observation_window_end: datetime | None = None
    event_ids: list[str] = Field(default_factory=list)

    def edge_key(self, source_id: str, target_id: str, edge_type: str) -> str:
        return f"{source_id}->{target_id}:{edge_type}"


class Metric(V2BaseModel):
    name: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    project_id: str | None = None
    position_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    value: float | int | None = None
    unit: str = ""
    sample_size: int = 0
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    definition_version: str = "v2.0"
    missing_reason: str | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)


class Finding(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    finding_type: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    affected_positions: list[str] = Field(default_factory=list)
    affected_projects: list[str] = Field(default_factory=list)
    supporting_metric_names: list[str] = Field(default_factory=list)
    supporting_event_ids: list[str] = Field(default_factory=list)
    detection_version: str = "v2.0"
    suggested_investigation: str = ""
    status: FindingStatus = "open"
    dismissed_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AtomicChange(V2BaseModel):
    change_type: Literal[
        "create_position",
        "archive_position",
        "update_position",
        "assign_occupant",
        "remove_occupant",
        "change_reporting_line",
        "move_position",
        "update_responsibilities",
        "grant_authority",
        "revoke_authority",
        "allocate_budget",
        "pause_project",
        "resume_project",
        "update_approval_policy",
        "create_temporary_team",
    ]
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    handoff_plan: str | None = None


class OrganizationChangeProposal(V2BaseModel):
    id: str = Field(min_length=1)
    baseline_snapshot_id: str = Field(min_length=1)
    proposer_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)
    atomic_changes: list[AtomicChange] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    expected_effects: dict[str, float | int | str] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    simulation_requirements: list[str] = Field(default_factory=list)
    approval_requirements: list[str] = Field(default_factory=list)
    evaluation_window_days: int = 14
    rollback_conditions: dict[str, float | int | str] = Field(default_factory=dict)
    affected_positions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    status: ProposalStatus = "proposed"
    validation_errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(V2BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    preview_state: OrganizationState | None = None


class SimulationResult(V2BaseModel):
    id: str = Field(min_length=1)
    proposal_id: str | None = None
    baseline_metric_values: dict[str, float | int | None] = Field(default_factory=dict)
    scenario_metric_values: dict[str, float | int | None] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    uncertainty: dict[str, tuple[float, float]] = Field(default_factory=dict)
    comparable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeApproval(V2BaseModel):
    id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    approver_position_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    rationale: str = ""
    decided_at: datetime = Field(default_factory=utc_now)


class DecisionOption(V2BaseModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    proposal_id: str | None = None


class ExpectedOutcome(V2BaseModel):
    metric_name: str = Field(min_length=1)
    expected_value: float
    comparator: Literal["lt", "lte", "gt", "gte", "eq"] = "lt"
    tolerance: float = 0.0


class ObservedOutcome(V2BaseModel):
    metric_name: str = Field(min_length=1)
    observed_value: float


class Decision(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    project_id: str | None = None
    question: str = Field(min_length=1)
    owner_position_id: str = Field(min_length=1)
    status: DecisionStatus = "draft"
    options: list[DecisionOption] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    dissent: dict[str, str] = Field(default_factory=dict)
    selected_option_id: str | None = None
    rationale: list[str] = Field(default_factory=list)
    assumptions: dict[str, Literal["true", "false", "unknown"]] = Field(default_factory=dict)
    expected_outcomes: list[ExpectedOutcome] = Field(default_factory=list)
    observed_outcomes: list[ObservedOutcome] = Field(default_factory=list)
    revisit_conditions: list[str] = Field(default_factory=list)
    superseded_by_decision_id: str | None = None
    evaluation: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None


class OrganizationSnapshot(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    source_event_cursor: str | None = None
    state: OrganizationState
    metrics_summary: dict[str, float | int | None] = Field(default_factory=dict)
    content_hash: str = Field(min_length=1)


class Experiment(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    baseline_snapshot_id: str = Field(min_length=1)
    applied_change_set_id: str = Field(min_length=1)
    baseline_time_window: tuple[datetime | None, datetime | None]
    treatment_time_window: tuple[datetime | None, datetime | None]
    target_metrics: list[str] = Field(default_factory=list)
    guardrail_metrics: list[str] = Field(default_factory=list)
    rollback_thresholds: dict[str, float] = Field(default_factory=dict)
    expected_effect: dict[str, float] = Field(default_factory=dict)
    observed_effect: dict[str, float] = Field(default_factory=dict)
    conclusion: Literal["retained", "rolled_back", "inconclusive"] | None = None
    status: ExperimentStatus = "planned"
    sample_size: int = 0
    confounding_events: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(V2BaseModel):
    id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    actor_position_id: str | None = None
    object_type: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    before_snapshot_id: str | None = None
    after_snapshot_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
