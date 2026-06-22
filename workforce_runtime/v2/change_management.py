from __future__ import annotations

from typing import Literal, cast

from workforce_runtime.v2.governance import ChangeValidator
from workforce_runtime.v2.models import (
    AuditRecord,
    ChangeApproval,
    NormalizedEvent,
    OccupancyType,
    Occupancy,
    Occupant,
    OrganizationChangeProposal,
    OrganizationState,
    ValidationResult,
    utc_now,
)
from workforce_runtime.v2.snapshot import SnapshotService
from workforce_runtime.v2.store import V2SQLiteStore


class ChangeApplicationError(RuntimeError):
    """Raised when a validated organization change cannot be applied transactionally."""


class ChangeManager:
    def __init__(self, *, store: V2SQLiteStore, snapshot_service: SnapshotService | None = None) -> None:
        self.store = store
        self.snapshot_service = snapshot_service or SnapshotService(store)
        self.validator = ChangeValidator()

    def approve(
        self,
        *,
        proposal: OrganizationChangeProposal,
        approver_position_id: str,
        decision: str = "approved",
        rationale: str = "",
    ) -> tuple[OrganizationChangeProposal, ChangeApproval]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision must be approved or rejected")
        typed_decision = cast(Literal["approved", "rejected"], decision)
        approval = ChangeApproval(
            id=f"approval_{proposal.id}_{approver_position_id}",
            proposal_id=proposal.id,
            approver_position_id=approver_position_id,
            decision=typed_decision,
            rationale=rationale,
        )
        next_status = "approved" if decision == "approved" else "rejected"
        updated = proposal.model_copy(update={"status": next_status})
        self.store.save_proposal(updated)
        self.store.save_object("approval", approval.id, approval)
        return updated, approval

    def apply(
        self,
        *,
        state: OrganizationState,
        proposal: OrganizationChangeProposal,
        approver_position_id: str,
    ) -> tuple[OrganizationState, list[AuditRecord]]:
        if proposal.status != "approved":
            raise ChangeApplicationError("proposal must be approved before application")
        validation = self.validator.validate(state=state, proposal=proposal, approver_position_id=approver_position_id)
        if not validation.ok:
            raise ChangeApplicationError("; ".join(validation.errors))
        before = self.snapshot_service.create_snapshot(
            organization_id=state.organization.id,
            state=state,
            reason=f"pre_change_{proposal.id}",
            source_event_cursor=self.store.latest_event_cursor(),
        )
        try:
            next_state = self._apply_changes(state.model_copy(deep=True), proposal)
            after = self.snapshot_service.create_snapshot(
                organization_id=state.organization.id,
                state=next_state,
                reason=f"post_change_{proposal.id}",
                source_event_cursor=self.store.latest_event_cursor(),
            )
            self.store.save_state(next_state)
            records = [
                AuditRecord(
                    id=f"audit_{proposal.id}_{index}",
                    organization_id=state.organization.id,
                    action=change.change_type,
                    actor_position_id=approver_position_id,
                    object_type="organization_change",
                    object_id=proposal.id,
                    before_snapshot_id=before.id,
                    after_snapshot_id=after.id,
                    metadata={"change": change.model_dump(mode="json")},
                )
                for index, change in enumerate(proposal.atomic_changes)
            ]
            for record in records:
                self.store.save_audit_record(record)
            self.store.save_event(
                NormalizedEvent(
                    id=f"event_change_applied_{proposal.id}",
                    organization_id=state.organization.id,
                    actor_position_id=approver_position_id,
                    event_type="organization_change_applied",
                    object_type="organization_change_proposal",
                    object_id=proposal.id,
                    source="workforce_runtime",
                    source_event_id=f"change_applied:{proposal.id}",
                    metadata={"before_snapshot_id": before.id, "after_snapshot_id": after.id},
                )
            )
            self.store.save_proposal(proposal.model_copy(update={"status": "active"}))
            return next_state, records
        except Exception as exc:
            raise ChangeApplicationError(str(exc)) from exc

    def validate(self, *, state: OrganizationState, proposal: OrganizationChangeProposal) -> ValidationResult:
        return self.validator.validate(state=state, proposal=proposal)

    def _apply_changes(self, state: OrganizationState, proposal: OrganizationChangeProposal) -> OrganizationState:
        for change in proposal.atomic_changes:
            if change.change_type == "create_position":
                from workforce_runtime.v2.models import Position

                position = Position.model_validate(change.payload)
                state.positions[position.id] = position
                continue
            if change.change_type == "archive_position" and change.target_id:
                state.positions[change.target_id] = state.positions[change.target_id].model_copy(
                    update={"status": "archived", "updated_at": utc_now()}
                )
                continue
            if change.change_type == "assign_occupant":
                position_id = change.target_id or str(change.payload.get("position_id") or "")
                occupant_payload = change.payload.get("occupant")
                occupant_id = str(change.payload.get("occupant_id") or "")
                if occupant_payload:
                    occupant = Occupant.model_validate(occupant_payload)
                    state.occupants[occupant.id] = occupant
                    occupant_id = occupant.id
                occupancy_type = str(change.payload.get("occupancy_type") or "primary")
                if occupancy_type not in {"primary", "acting", "backup", "delegate", "mirror"}:
                    raise ChangeApplicationError(f"unsupported occupancy type: {occupancy_type}")
                occupancy = Occupancy(
                    id=f"occupancy_{position_id}_{occupant_id}_{int(utc_now().timestamp())}",
                    position_id=position_id,
                    occupant_id=occupant_id,
                    occupancy_type=cast(OccupancyType, occupancy_type),
                    effective_from=utc_now(),
                    status="active",
                    handoff_artifact_id=change.handoff_plan,
                )
                state.occupancies[occupancy.id] = occupancy
                continue
            if change.change_type == "change_reporting_line" and change.target_id:
                state.positions[change.target_id] = state.positions[change.target_id].model_copy(
                    update={"reports_to_position_id": change.payload.get("reports_to_position_id"), "updated_at": utc_now()}
                )
                continue
            if change.change_type == "update_approval_policy":
                policy_id = change.target_id or str(change.payload.get("policy_id") or "approval_policy")
                state.policies[policy_id] = {**state.policies.get(policy_id, {}), **change.payload}
                continue
            if change.change_type == "update_responsibilities" and change.target_id:
                state.positions[change.target_id] = state.positions[change.target_id].model_copy(
                    update={"responsibilities": change.payload["responsibilities"], "updated_at": utc_now()}
                )
                continue
        return state
