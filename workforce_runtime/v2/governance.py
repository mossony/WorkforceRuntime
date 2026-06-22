from __future__ import annotations

from typing import Any

from workforce_runtime.v2.models import (
    AtomicChange,
    Finding,
    Metric,
    OrganizationChangeProposal,
    OrganizationSnapshot,
    OrganizationState,
    Position,
    ValidationResult,
    utc_now,
)
from workforce_runtime.v2.organization import validate_organization_state


class ChangeValidator:
    def validate(
        self,
        *,
        state: OrganizationState,
        proposal: OrganizationChangeProposal,
        approver_position_id: str | None = None,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if not proposal.atomic_changes:
            errors.append("proposal must contain at least one atomic change")
        preview = state.model_copy(deep=True)
        for change in proposal.atomic_changes:
            try:
                self._preview_change(preview, change)
            except ValueError as exc:
                errors.append(str(exc))
        invariant_result = validate_organization_state(preview)
        errors.extend(invariant_result.errors)
        warnings.extend(invariant_result.warnings)
        if proposal.risk_level == "high" and "human" not in proposal.approval_requirements:
            errors.append("high-risk changes require human approval")
        if approver_position_id is not None and approver_position_id not in state.positions:
            errors.append(f"unknown approver position {approver_position_id}")
        return ValidationResult(ok=not errors, errors=errors, warnings=warnings, preview_state=preview if not errors else None)

    def _preview_change(self, state: OrganizationState, change: AtomicChange) -> None:
        if change.change_type == "create_position":
            position = Position.model_validate(change.payload)
            if position.id in state.positions:
                raise ValueError(f"position {position.id} already exists")
            state.positions[position.id] = position
            return
        if change.change_type == "archive_position":
            if not change.target_id or change.target_id not in state.positions:
                raise ValueError(f"archive_position target does not exist: {change.target_id}")
            active_runs = [
                run.id
                for run in state.worker_runs.values()
                if run.position_id == change.target_id and run.status in {"queued", "running"}
            ]
            if active_runs and not change.handoff_plan:
                raise ValueError(f"archive_position requires handoff plan for active work: {active_runs}")
            position = state.positions[change.target_id]
            state.positions[change.target_id] = position.model_copy(update={"status": "archived", "updated_at": utc_now()})
            return
        if change.change_type == "assign_occupant":
            position_id = change.target_id or str(change.payload.get("position_id") or "")
            occupant_id = str(change.payload.get("occupant_id") or "")
            if position_id not in state.positions:
                raise ValueError(f"assign_occupant target position does not exist: {position_id}")
            if occupant_id not in state.occupants:
                raise ValueError(f"assign_occupant occupant does not exist: {occupant_id}")
            return
        if change.change_type == "change_reporting_line":
            position_id = change.target_id or str(change.payload.get("position_id") or "")
            manager_id = change.payload.get("reports_to_position_id")
            if position_id not in state.positions:
                raise ValueError(f"change_reporting_line target does not exist: {position_id}")
            if manager_id is not None and manager_id not in state.positions:
                raise ValueError(f"change_reporting_line manager does not exist: {manager_id}")
            state.positions[position_id] = state.positions[position_id].model_copy(
                update={"reports_to_position_id": manager_id, "updated_at": utc_now()}
            )
            return
        if change.change_type == "update_approval_policy":
            policy_id = change.target_id or str(change.payload.get("policy_id") or "approval_policy")
            state.policies[policy_id] = {**state.policies.get(policy_id, {}), **change.payload}
            return
        if change.change_type == "update_responsibilities":
            position_id = change.target_id or str(change.payload.get("position_id") or "")
            responsibilities = change.payload.get("responsibilities")
            if position_id not in state.positions:
                raise ValueError(f"update_responsibilities target does not exist: {position_id}")
            if not isinstance(responsibilities, list) or not responsibilities:
                raise ValueError("update_responsibilities requires non-empty responsibilities list")
            state.positions[position_id] = state.positions[position_id].model_copy(
                update={"responsibilities": responsibilities, "updated_at": utc_now()}
            )
            return
        if change.change_type in {"allocate_budget", "grant_authority", "revoke_authority", "pause_project", "resume_project"}:
            return
        raise ValueError(f"unsupported atomic change in preview: {change.change_type}")


class RuleBasedGovernor:
    def __init__(self, *, enabled_rules: list[str] | None = None) -> None:
        self.enabled_rules = set(enabled_rules or ["approval_bottleneck", "hidden_dependency", "overloaded_position"])

    def inspect(
        self,
        *,
        snapshot: OrganizationSnapshot,
        metrics: list[Metric],
        findings: list[Finding],
    ) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot.id,
            "open_findings": [finding.id for finding in findings if finding.status == "open"],
            "metric_names": [metric.name for metric in metrics],
            "assessment": "structured findings available" if findings else "no material structural issue detected",
        }

    def propose_changes(
        self,
        *,
        assessment: dict[str, Any],
        snapshot: OrganizationSnapshot,
        findings: list[Finding],
    ) -> list[OrganizationChangeProposal]:
        proposals: list[OrganizationChangeProposal] = []
        for finding in findings:
            if finding.finding_type == "approval_bottleneck" and "approval_bottleneck" in self.enabled_rules:
                proposals.extend(self._approval_bottleneck_proposals(snapshot, finding))
            if finding.finding_type == "hidden_dependency" and "hidden_dependency" in self.enabled_rules:
                proposals.append(self._hidden_dependency_proposal(snapshot, finding))
            if finding.finding_type == "overloaded_position" and "overloaded_position" in self.enabled_rules:
                proposals.append(self._overloaded_position_proposal(snapshot, finding))
        return proposals

    def _approval_bottleneck_proposals(
        self,
        snapshot: OrganizationSnapshot,
        finding: Finding,
    ) -> list[OrganizationChangeProposal]:
        target = finding.affected_positions[0]
        return [
            OrganizationChangeProposal(
                id=f"proposal_low_risk_auto_approval_{target}",
                baseline_snapshot_id=snapshot.id,
                proposer_id="rule_based_governor",
                finding_ids=[finding.id],
                atomic_changes=[
                    AtomicChange(
                        change_type="update_approval_policy",
                        target_id="low_risk_approval",
                        payload={"policy_id": "low_risk_approval", "low_risk_auto_approve": True, "verifier_required": True},
                    )
                ],
                rationale=[
                    "Approval latency and approval concentration indicate low-risk work is waiting on one position.",
                    "Low rejection rate suggests risk-based automatic approval can reduce queue time.",
                ],
                expected_effects={"median_approval_latency_change": -0.35, "quality_change": -0.01},
                risks=["Incorrect low-risk classification may allow a bad artifact through."],
                assumptions=["Low-risk classifier or verifier is available."],
                simulation_requirements=["approval_latency"],
                approval_requirements=["manager"],
                affected_positions=[target],
                risk_level="medium",
                rollback_conditions={"rejection_rate_increase": 0.05},
            ),
            OrganizationChangeProposal(
                id=f"proposal_add_backup_reviewer_{target}",
                baseline_snapshot_id=snapshot.id,
                proposer_id="rule_based_governor",
                finding_ids=[finding.id],
                atomic_changes=[
                    AtomicChange(
                        change_type="create_position",
                        payload={
                            "id": f"{target}_backup_reviewer",
                            "organization_id": snapshot.organization_id,
                            "department_id": snapshot.state.positions[target].department_id,
                            "title": "Backup Reviewer",
                            "description": "Handles review overflow for the bottlenecked approver.",
                            "reports_to_position_id": snapshot.state.positions[target].reports_to_position_id,
                            "responsibilities": ["review overflow", "low-risk approvals"],
                            "required_capabilities": ["code_review"],
                            "authority_policy_ids": ["review_overflow"],
                            "budget_account_id": f"budget_{target}_backup_reviewer",
                            "status": "active",
                        },
                    )
                ],
                rationale=["Adding a backup reviewer reduces queue concentration without removing approval controls."],
                expected_effects={"median_approval_latency_change": -0.25, "manager_load_change": -0.2},
                risks=["New reviewer may need calibration and supervision."],
                assumptions=["A suitable occupant can be assigned."],
                simulation_requirements=["approval_latency", "capacity"],
                approval_requirements=["department_leader"],
                affected_positions=[target],
                risk_level="low",
            ),
        ]

    def _hidden_dependency_proposal(
        self,
        snapshot: OrganizationSnapshot,
        finding: Finding,
    ) -> OrganizationChangeProposal:
        target = finding.affected_positions[0]
        return OrganizationChangeProposal(
            id=f"proposal_backup_for_hidden_dependency_{target}",
            baseline_snapshot_id=snapshot.id,
            proposer_id="rule_based_governor",
            finding_ids=[finding.id],
            atomic_changes=[
                AtomicChange(
                    change_type="create_position",
                    payload={
                        "id": f"{target}_backup",
                        "organization_id": snapshot.organization_id,
                        "department_id": snapshot.state.positions[target].department_id,
                        "title": f"Backup for {snapshot.state.positions[target].title}",
                        "description": "Provides backup coverage for a high-centrality dependency.",
                        "reports_to_position_id": snapshot.state.positions[target].reports_to_position_id,
                        "responsibilities": ["backup coverage", "handoff readiness"],
                        "required_capabilities": snapshot.state.positions[target].required_capabilities,
                        "authority_policy_ids": [],
                        "budget_account_id": f"budget_{target}_backup",
                        "status": "active",
                    },
                )
            ],
            rationale=["The position is central to work but has no formal authority or backup."],
            expected_effects={"blocked_duration_change": -0.2, "bus_factor_change": 1},
            risks=["Backup position may be underused if the dependency is temporary."],
            assumptions=["The target position can document handoff context."],
            simulation_requirements=["capacity", "blocked_duration"],
            approval_requirements=["manager"],
            affected_positions=[target],
            risk_level="low",
        )

    def _overloaded_position_proposal(
        self,
        snapshot: OrganizationSnapshot,
        finding: Finding,
    ) -> OrganizationChangeProposal:
        target = finding.affected_positions[0]
        return OrganizationChangeProposal(
            id=f"proposal_route_overflow_{target}",
            baseline_snapshot_id=snapshot.id,
            proposer_id="rule_based_governor",
            finding_ids=[finding.id],
            atomic_changes=[
                AtomicChange(
                    change_type="update_approval_policy",
                    target_id="task_routing",
                    payload={"policy_id": "task_routing", "overflow_from": target, "max_parallel_items": 3},
                )
            ],
            rationale=["The position receives enough incoming work to justify an overflow routing rule."],
            expected_effects={"queue_time_change": -0.18, "human_attention_change": -0.1},
            risks=["Routing may increase coordination cost."],
            assumptions=["Overflow destination exists or can be configured."],
            simulation_requirements=["queue_time", "capacity"],
            approval_requirements=["manager"],
            affected_positions=[target],
            risk_level="medium",
        )
