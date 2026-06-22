from __future__ import annotations

import hashlib
import json

from workforce_runtime.v2.models import OrganizationSnapshot, OrganizationState
from workforce_runtime.v2.store import V2SQLiteStore


def state_content_hash(state: OrganizationState, metrics_summary: dict[str, float | int | None] | None = None) -> str:
    payload = {
        "state": state.model_dump(mode="json"),
        "metrics_summary": metrics_summary or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SnapshotService:
    def __init__(self, store: V2SQLiteStore) -> None:
        self.store = store

    def create_snapshot(
        self,
        *,
        organization_id: str,
        state: OrganizationState,
        reason: str,
        metrics_summary: dict[str, float | int | None] | None = None,
        source_event_cursor: str | None = None,
        snapshot_id: str | None = None,
    ) -> OrganizationSnapshot:
        digest = state_content_hash(state, metrics_summary)
        snapshot = OrganizationSnapshot(
            id=snapshot_id or f"snapshot_{digest[:12]}",
            organization_id=organization_id,
            reason=reason,
            source_event_cursor=source_event_cursor,
            state=state.model_copy(deep=True),
            metrics_summary=metrics_summary or {},
            content_hash=digest,
        )
        self.store.save_snapshot(snapshot)
        return snapshot

    def load_snapshot(self, snapshot_id: str) -> OrganizationSnapshot | None:
        return self.store.get_snapshot(snapshot_id)

    def structural_diff(self, before: OrganizationSnapshot, after: OrganizationSnapshot) -> dict[str, list[str]]:
        before_positions = before.state.positions
        after_positions = after.state.positions
        before_occupancies = before.state.occupancies
        after_occupancies = after.state.occupancies
        before_policies = before.state.policies
        after_policies = after.state.policies
        return {
            "positions_added": sorted(set(after_positions) - set(before_positions)),
            "positions_removed": sorted(set(before_positions) - set(after_positions)),
            "positions_changed": sorted(
                position_id
                for position_id in set(before_positions) & set(after_positions)
                if before_positions[position_id] != after_positions[position_id]
            ),
            "occupancies_added": sorted(set(after_occupancies) - set(before_occupancies)),
            "occupancies_removed": sorted(set(before_occupancies) - set(after_occupancies)),
            "occupancies_changed": sorted(
                occupancy_id
                for occupancy_id in set(before_occupancies) & set(after_occupancies)
                if before_occupancies[occupancy_id] != after_occupancies[occupancy_id]
            ),
            "policies_changed": sorted(
                policy_id
                for policy_id in set(before_policies) | set(after_policies)
                if before_policies.get(policy_id) != after_policies.get(policy_id)
            ),
        }
