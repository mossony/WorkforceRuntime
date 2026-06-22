from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core import AgentProfile, Event, TaskContract
from workforce_runtime.storage import SQLiteStore
from workforce_runtime.v2.findings import FindingDetector
from workforce_runtime.v2.governance import ChangeValidator, RuleBasedGovernor
from workforce_runtime.v2.metrics import MetricsEngine
from workforce_runtime.v2.models import (
    Finding,
    Metric,
    NormalizedEvent,
    Organization,
    OrganizationChangeProposal,
    OrganizationSnapshot,
    OrganizationState,
    Project,
    SimulationResult,
    WorkGraph,
)
from workforce_runtime.v2.organization import migrate_agents, position_id_for, validate_organization_state
from workforce_runtime.v2.simulation import HistoricalReplaySimulator
from workforce_runtime.v2.snapshot import SnapshotService
from workforce_runtime.v2.store import V2SQLiteStore
from workforce_runtime.v2.work_graph import WorkGraphBuilder


class V1V2AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    task_id: str | None = None
    analyzed_task_ids: list[str] = Field(default_factory=list)
    normalized_events: list[NormalizedEvent] = Field(default_factory=list)
    work_graph: WorkGraph
    metrics: list[Metric] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    proposals: list[OrganizationChangeProposal] = Field(default_factory=list)
    simulations: list[SimulationResult] = Field(default_factory=list)
    baseline_snapshot: OrganizationSnapshot
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def analyze_v1_runtime(
    *,
    v1_db_path: str | Path,
    task_id: str | None = None,
    v2_db_path: str | Path | None = None,
    export_dir: str | Path | None = None,
) -> V1V2AnalysisResult:
    with SQLiteStore(v1_db_path) as v1_store:
        agents = v1_store.list_agents()
        tasks = v1_store.list_tasks()
        events = v1_store.list_events()
        if task_id is not None:
            selected_task_ids = _task_family_ids(tasks, task_id)
            events = [
                event
                for event in events
                if event.task_id in selected_task_ids
                or str(event.payload.get("task_id") or "") in selected_task_ids
                or str(event.payload.get("parent_task_id") or "") in selected_task_ids
                or str(event.payload.get("reviewed_task_id") or "") in selected_task_ids
            ]
        else:
            selected_task_ids = [task.task_id for task in tasks]
        state = _build_v2_state_from_v1(agents=agents, tasks=tasks, selected_task_ids=selected_task_ids)
        normalized_events = normalize_v1_events(
            events=events,
            agents=agents,
            tasks=tasks,
            organization_id=state.organization.id,
            project_id=_project_id(task_id),
        )

    v2_db = Path(v2_db_path) if v2_db_path is not None else Path(v1_db_path)
    with V2SQLiteStore(v2_db) as v2_store:
        v2_store.save_state(state)
        v2_store.save_events(normalized_events)
        graph = WorkGraphBuilder().build(
            organization_id=state.organization.id,
            state=state,
            events=normalized_events,
        )
        v2_store.save_work_graph(graph)
        metrics = MetricsEngine().calculate(state=state, events=normalized_events, graph=graph)
        snapshot_service = SnapshotService(v2_store)
        baseline_snapshot = snapshot_service.create_snapshot(
            organization_id=state.organization.id,
            state=state,
            reason="v1_runtime_analysis",
            metrics_summary={metric.name: metric.value for metric in metrics},
            source_event_cursor=v2_store.latest_event_cursor(),
        )
        findings = FindingDetector().detect(state=state, graph=graph, metrics=metrics)
        findings.extend(_detect_v1_specific_findings(state=state, events=normalized_events, metrics=metrics))
        findings = _dedupe_findings(findings)
        for finding in findings:
            v2_store.save_finding(finding)
        governor = RuleBasedGovernor()
        assessment = governor.inspect(snapshot=baseline_snapshot, metrics=metrics, findings=findings)
        proposals = governor.propose_changes(assessment=assessment, snapshot=baseline_snapshot, findings=findings)
        validator = ChangeValidator()
        validated: list[OrganizationChangeProposal] = []
        for proposal in proposals:
            validation = validator.validate(state=state, proposal=proposal)
            updated = proposal.model_copy(
                update={
                    "status": "validated" if validation.ok else "proposed",
                    "validation_errors": validation.errors,
                }
            )
            v2_store.save_proposal(updated)
            validated.append(updated)
        simulator = HistoricalReplaySimulator()
        simulations = [simulator.baseline(metrics=metrics)]
        simulations.extend(
            simulator.simulate(proposal=proposal, baseline_metrics=metrics)
            for proposal in validated
            if proposal.status == "validated"
        )
        for simulation in simulations:
            v2_store.save_simulation_result(simulation)

    result = V1V2AnalysisResult(
        organization_id=state.organization.id,
        task_id=task_id,
        analyzed_task_ids=selected_task_ids,
        normalized_events=normalized_events,
        work_graph=graph,
        metrics=metrics,
        findings=findings,
        proposals=validated,
        simulations=simulations,
        baseline_snapshot=baseline_snapshot,
        recommendations=_recommendations(findings, validated),
        warnings=_analysis_warnings(state=state, normalized_events=normalized_events),
    )
    if export_dir is not None:
        export_v1_v2_analysis(result, Path(export_dir))
    return result


def normalize_v1_events(
    *,
    events: list[Event],
    agents: list[AgentProfile],
    tasks: list[TaskContract],
    organization_id: str,
    project_id: str,
) -> list[NormalizedEvent]:
    agent_position = {agent.id: position_id_for(agent) for agent in agents}
    agent_occupant = {agent.id: f"occupant_{_slug(agent.id)}" for agent in agents}
    tasks_by_id = {task.task_id: task for task in tasks}
    normalized: list[NormalizedEvent] = []
    for event in events:
        event_type = _normalize_event_type(event)
        object_type, object_id = _object_for_event(event)
        actor_position_id = agent_position.get(event.actor_id)
        actor_occupant_id = agent_occupant.get(event.actor_id)
        target_agent_id = _target_agent_id(event, tasks_by_id)
        target_position_id = agent_position.get(target_agent_id or "")
        target_occupant_id = agent_occupant.get(target_agent_id or "")
        normalized.append(
            NormalizedEvent(
                id=f"v1_{event.event_id}",
                organization_id=organization_id,
                project_id=project_id,
                actor_position_id=actor_position_id,
                actor_occupant_id=actor_occupant_id,
                target_position_id=target_position_id,
                target_occupant_id=target_occupant_id,
                event_type=event_type,
                object_type=object_type,
                object_id=object_id,
                occurred_at=event.timestamp,
                observed_at=event.timestamp,
                source="v1_runtime",
                source_event_id=event.event_id,
                task_id=event.task_id,
                metadata={
                    "v1_event_type": event.event_type,
                    "payload": event.payload,
                    "task_category": _task_category(event, tasks_by_id),
                    **_usage_metadata(event),
                },
            )
        )
    return normalized


def export_v1_v2_analysis(result: V1V2AnalysisResult, export_dir: Path) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "v2_analysis.json").write_text(result.model_dump_json(indent=2))
    (export_dir / "normalized_events.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in result.normalized_events) + "\n"
    )
    (export_dir / "work_graph.json").write_text(result.work_graph.model_dump_json(indent=2))
    (export_dir / "metrics.json").write_text(json.dumps([metric.model_dump(mode="json") for metric in result.metrics], indent=2))
    (export_dir / "findings.json").write_text(json.dumps([finding.model_dump(mode="json") for finding in result.findings], indent=2))
    (export_dir / "change_proposals.json").write_text(
        json.dumps([proposal.model_dump(mode="json") for proposal in result.proposals], indent=2)
    )
    (export_dir / "simulation_results.json").write_text(
        json.dumps([simulation.model_dump(mode="json") for simulation in result.simulations], indent=2)
    )
    (export_dir / "recommendations.md").write_text("\n".join(f"- {item}" for item in result.recommendations) + "\n")


def _build_v2_state_from_v1(
    *,
    agents: list[AgentProfile],
    tasks: list[TaskContract],
    selected_task_ids: list[str],
) -> OrganizationState:
    organization = Organization(
        id="v1_runtime_org",
        name="V1 Runtime Organization",
        mission="Analyze a completed V1 Workforce Runtime run with V2 governance metrics.",
    )
    state = migrate_agents(agents, organization=organization)
    state.projects[_project_id(selected_task_ids[0] if selected_task_ids else None)] = Project(
        id=_project_id(selected_task_ids[0] if selected_task_ids else None),
        organization_id=organization.id,
        name="V1 analyzed task run",
        owner_position_id=_root_position_id(agents),
        success_metrics=["median_task_cycle_time", "median_approval_latency", "authority_work_mismatch"],
    )
    for task in tasks:
        if task.task_id not in selected_task_ids:
            continue
        if task.assigned_to:
            position_id = position_id_for(next(agent for agent in agents if agent.id == task.assigned_to))
            state.worker_runs[f"v1_task_{task.task_id}"] = state.worker_runs.get(
                f"v1_task_{task.task_id}",
                _worker_run_record(
                    task_id=task.task_id,
                    position_id=position_id,
                    occupant_id=f"occupant_{_slug(task.assigned_to)}",
                    status="completed" if task.status == "completed" else "failed" if task.status == "failed" else "running",
                ),
            )
    validation = validate_organization_state(state)
    if not validation.ok:
        state.metadata["validation_errors"] = validation.errors
    return state


def _worker_run_record(*, task_id: str, position_id: str, occupant_id: str, status: str):
    from workforce_runtime.v2.models import WorkerRunRecord

    return WorkerRunRecord(
        id=f"worker_run_{task_id}",
        occupant_id=occupant_id,
        position_id=position_id,
        assignment_id=f"assignment_{task_id}",
        task_id=task_id,
        project_id=_project_id(task_id),
        backend="v1_runtime",
        status=status,  # type: ignore[arg-type]
    )


def _normalize_event_type(event: Event) -> str:
    if event.event_type == "task_status_updated":
        status = str(event.payload.get("status") or "")
        return {
            "assigned": "task_assigned",
            "in_progress": "task_started",
            "blocked": "task_blocked",
            "completed": "task_completed",
            "failed": "task_failed",
            "cancelled": "task_failed",
        }.get(status, "task_started")
    return {
        "report_registered": "report_submitted",
        "artifact_registered": "artifact_created",
        "manager_review_created": "review_requested",
        "manager_review_decided": "review_completed",
        "human_report_registered": "human_intervention",
        "discussion_message": "message_sent",
        "progress_check_requested": "review_requested",
        "budget_requested": "budget_requested",
        "tool_request_submitted": "approval_requested",
        "tool_request_decided": "approval_granted",
        "worker_run_finished": "worker_run_completed",
        "agent_run_finished": "worker_run_completed",
    }.get(event.event_type, event.event_type)


def _object_for_event(event: Event) -> tuple[str, str]:
    payload = event.payload
    if event.event_type == "report_registered":
        return "report", str(payload.get("report_id") or event.event_id)
    if event.event_type == "artifact_registered":
        return "artifact", str(payload.get("artifact_id") or event.event_id)
    if event.event_type in {"worker_run_started", "worker_run_finished", "agent_run_started", "agent_run_finished"}:
        return "worker_run", str(payload.get("run_id") or event.event_id)
    if event.event_type.startswith("task_") and event.task_id:
        return "task", event.task_id
    if event.event_type.startswith("manager_review") and event.task_id:
        return "review", event.task_id
    return "event", event.event_id


def _target_agent_id(event: Event, tasks_by_id: dict[str, TaskContract]) -> str | None:
    payload = event.payload
    for key in ("assigned_to", "to_agent_id", "target_agent_id", "reviewer_id"):
        value = payload.get(key)
        if value:
            return str(value)
    reviewed_task_id = payload.get("reviewed_task_id")
    if reviewed_task_id and str(reviewed_task_id) in tasks_by_id:
        return tasks_by_id[str(reviewed_task_id)].assigned_to
    if event.task_id and event.task_id in tasks_by_id:
        task = tasks_by_id[event.task_id]
        if event.actor_id == task.assigned_by:
            return task.assigned_to
        if event.actor_id == task.assigned_to:
            return task.assigned_by
    return None


def _detect_v1_specific_findings(
    *,
    state: OrganizationState,
    events: list[NormalizedEvent],
    metrics: list[Metric],
) -> list[Finding]:
    findings: list[Finding] = []
    review_events = [event for event in events if event.event_type in {"review_requested", "review_completed"}]
    approvals = [event for event in events if event.event_type in {"approval_requested", "approval_granted"}]
    if len(review_events) >= 2:
        affected = sorted({event.actor_position_id for event in review_events if event.actor_position_id})
        findings.append(
            Finding(
                id="finding_v1_manager_review_overhead",
                organization_id=state.organization.id,
                finding_type="manager_review_overhead",
                severity="medium",
                confidence=0.72,
                affected_positions=affected,
                supporting_metric_names=["median_review_latency"],
                supporting_event_ids=[event.id for event in review_events],
                suggested_investigation="Check whether manager review is adding useful rejection signal or only queue overhead.",
                metadata={"review_event_count": len(review_events)},
            )
        )
    if len(approvals) >= 2:
        findings.append(
            Finding(
                id="finding_v1_approval_queue",
                organization_id=state.organization.id,
                finding_type="approval_bottleneck",
                severity="medium",
                confidence=0.7,
                affected_positions=sorted({event.actor_position_id for event in approvals if event.actor_position_id}),
                supporting_metric_names=["median_approval_latency"],
                supporting_event_ids=[event.id for event in approvals],
                suggested_investigation="Consider automatic approval for low-risk transitions with valid reports and final review preserved.",
                metadata={"approval_event_count": len(approvals)},
            )
        )
    worker_outputs = [event for event in events if event.metadata.get("v1_event_type") == "worker_output"]
    worker_failures = [
        event
        for event in events
        if event.metadata.get("v1_event_type") in {"worker_run_finished", "agent_run_finished"}
        and _event_returncode(event) not in {None, 0}
    ]
    task_failures = [event for event in events if event.event_type == "task_failed"]
    artifacts = [event for event in events if event.event_type == "artifact_created"]
    has_git_diff_artifact = any(str((event.metadata.get("payload") or {}).get("type") or "") == "git_diff" for event in artifacts)
    affected_workers = sorted(
        {
            event.actor_position_id
            for event in [*worker_failures, *task_failures, *worker_outputs]
            if event.actor_position_id
        }
    )
    if worker_failures or task_failures or (worker_outputs and not has_git_diff_artifact):
        findings.append(
            Finding(
                id="finding_v1_worker_execution_failed",
                organization_id=state.organization.id,
                finding_type="worker_execution_failed",
                severity="high",
                confidence=0.88,
                affected_positions=affected_workers,
                supporting_metric_names=["worker_success_rate"],
                supporting_event_ids=[event.id for event in [*worker_failures, *task_failures, *artifacts][:20]],
                suggested_investigation=(
                    "Inspect worker trajectory for whether the agent produced a patch, exited cleanly, "
                    "and reported validation evidence before manager review."
                ),
                metadata={
                    "worker_failure_count": len(worker_failures),
                    "task_failure_count": len(task_failures),
                    "artifact_count": len(artifacts),
                    "has_git_diff_artifact": has_git_diff_artifact,
                },
            )
        )
    output_text = "\n".join(str((event.metadata.get("payload") or {}).get("text") or "") for event in worker_outputs)
    diagnostic_terms = ["MRO", "__dict__", "__slots__", "class Printable", "has __dict__? True"]
    if worker_outputs and not has_git_diff_artifact and any(term in output_text for term in diagnostic_terms):
        findings.append(
            Finding(
                id="finding_v1_diagnosis_without_patch",
                organization_id=state.organization.id,
                finding_type="diagnosis_without_patch",
                severity="high",
                confidence=0.84,
                affected_positions=affected_workers,
                supporting_metric_names=["cycle_time", "artifact_completion_rate"],
                supporting_event_ids=[event.id for event in worker_outputs[-20:]],
                suggested_investigation=(
                    "The worker gathered useful diagnosis evidence but did not transition into editing. "
                    "Add manager checkpoints or split diagnosis and implementation into separate assignments."
                ),
                metadata={
                    "worker_output_event_count": len(worker_outputs),
                    "diagnostic_terms_seen": [term for term in diagnostic_terms if term in output_text],
                },
            )
        )
    failed_command_markers = ["exit_code\":1", "exit_code\":2", "exit_code\":127", "unrecognized flag", "No such file or directory"]
    failed_command_count = sum(output_text.count(marker) for marker in failed_command_markers)
    if failed_command_count >= 2:
        findings.append(
            Finding(
                id="finding_v1_tool_usage_friction",
                organization_id=state.organization.id,
                finding_type="tool_usage_friction",
                severity="medium",
                confidence=0.8,
                affected_positions=affected_workers,
                supporting_metric_names=["failed_tool_command_count"],
                supporting_event_ids=[event.id for event in worker_outputs[-30:]],
                suggested_investigation=(
                    "Worker repeated avoidable shell/tool errors. Provide tool-use constraints, "
                    "command examples, or a manager progress check after repeated command failures."
                ),
                metadata={"failed_command_count": failed_command_count},
            )
        )
    return findings


def _recommendations(findings: list[Finding], proposals: list[OrganizationChangeProposal]) -> list[str]:
    recommendations: list[str] = []
    for finding in findings:
        if finding.finding_type in {"approval_bottleneck", "manager_review_overhead"}:
            recommendations.append(
                "Reduce intermediate manager approvals for low-risk phase transitions, but keep final review/manager sign-off."
            )
        if finding.finding_type == "overloaded_position":
            recommendations.append("Split or route overflow work away from overloaded positions.")
        if finding.finding_type == "hidden_dependency":
            recommendations.append("Add backup coverage or explicit handoff docs for hidden dependency positions.")
        if finding.finding_type == "worker_execution_failed":
            recommendations.append(
                "Add a manager checkpoint before final timeout: require patch presence, validation evidence, and retry/escalation when a worker exits without artifacts."
            )
        if finding.finding_type == "diagnosis_without_patch":
            recommendations.append(
                "Split coding work into investigator, implementer, and reviewer roles or require handoff from diagnosis to edit after a bounded number of commands."
            )
        if finding.finding_type == "tool_usage_friction":
            recommendations.append(
                "Give workers repo-specific tool instructions and trigger manager intervention after repeated failed commands."
            )
    for proposal in proposals:
        if proposal.status == "validated":
            recommendations.append(f"Candidate validated proposal: {proposal.id}")
    return sorted(set(recommendations))


def _event_returncode(event: NormalizedEvent) -> int | None:
    payload = event.metadata.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("returncode")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _analysis_warnings(*, state: OrganizationState, normalized_events: list[NormalizedEvent]) -> list[str]:
    warnings: list[str] = []
    if not normalized_events:
        warnings.append("No V1 events were available for V2 analysis.")
    if not any(event.event_type == "task_completed" for event in normalized_events):
        warnings.append("No completed task event was observed; organizational outcome may be incomplete.")
    validation = validate_organization_state(state)
    warnings.extend(validation.warnings)
    warnings.extend(f"state validation error: {error}" for error in validation.errors)
    return warnings


def _task_family_ids(tasks: list[TaskContract], root_task_id: str) -> list[str]:
    selected = {root_task_id}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task.parent_task_id in selected and task.task_id not in selected:
                selected.add(task.task_id)
                changed = True
    return sorted(selected)


def _project_id(task_id: str | None) -> str:
    return f"project_v1_{_slug(task_id or 'all')}"


def _root_position_id(agents: list[AgentProfile]) -> str:
    root = next((agent for agent in agents if agent.manager_id is None), agents[0] if agents else None)
    return position_id_for(root) if root is not None else "position_runtime"


def _task_category(event: Event, tasks_by_id: dict[str, TaskContract]) -> str:
    if event.task_id and event.task_id in tasks_by_id:
        title = tasks_by_id[event.task_id].title.lower()
        if "review" in title:
            return "review"
        if "implement" in title or "fix" in title or "patch" in title:
            return "implementation"
        if "investigat" in title or "research" in title:
            return "investigation"
    return "runtime"


def _usage_metadata(event: Event) -> dict[str, Any]:
    usage = event.payload.get("usage")
    if isinstance(usage, dict):
        tokens = usage.get("input_tokens") or 0
        tokens += usage.get("output_tokens") or 0
        return {"tokens_used": tokens}
    return {}


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    by_id: dict[str, Finding] = {}
    for finding in findings:
        by_id[finding.id] = finding
    return list(by_id.values())


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "item"
