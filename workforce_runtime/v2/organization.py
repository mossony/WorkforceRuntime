from __future__ import annotations

from dataclasses import dataclass

from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.v2.models import (
    Department,
    Occupancy,
    Occupant,
    Organization,
    OrganizationState,
    Position,
    ValidationResult,
    utc_now,
)


@dataclass(frozen=True)
class AgentMigrationResult:
    position: Position
    occupant: Occupant
    occupancy: Occupancy


def slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "item"


def department_id_for(agent: AgentProfile) -> str:
    return slug(agent.department)


def position_id_for(agent: AgentProfile) -> str:
    return f"position_{slug(agent.id)}"


def occupant_id_for(agent: AgentProfile) -> str:
    return f"occupant_{slug(agent.id)}"


def migrate_agent_profile(
    agent: AgentProfile,
    *,
    organization_id: str,
    manager_position_id: str | None = None,
) -> AgentMigrationResult:
    """Convert a V1 AgentProfile into durable V2 role, worker, and occupancy records."""

    now = utc_now()
    position = Position(
        id=position_id_for(agent),
        organization_id=organization_id,
        department_id=department_id_for(agent),
        title=agent.role,
        description=f"Migrated from AgentProfile {agent.id}",
        reports_to_position_id=manager_position_id,
        responsibilities=list(agent.responsibilities),
        required_capabilities=[str(permission) for permission in agent.permissions],
        budget_account_id=f"budget_{slug(agent.id)}",
        status="active",
        created_at=now,
        updated_at=now,
        metadata={"source_agent_id": agent.id, "worker_type": agent.worker_type},
    )
    occupant = Occupant(
        id=occupant_id_for(agent),
        occupant_type="ai_worker" if agent.worker_type != "human" else "human",
        display_name=agent.name,
        worker_definition_id=agent.worker_type,
        capabilities=[str(permission) for permission in agent.permissions],
        status="available" if agent.status == "idle" else "busy",
        metadata={"source_agent_id": agent.id, "model": agent.model},
    )
    occupancy = Occupancy(
        id=f"occupancy_{slug(agent.id)}_primary",
        position_id=position.id,
        occupant_id=occupant.id,
        occupancy_type="primary",
        effective_from=now,
        status="active",
    )
    return AgentMigrationResult(position=position, occupant=occupant, occupancy=occupancy)


def migrate_agents(
    agents: list[AgentProfile],
    *,
    organization: Organization,
) -> OrganizationState:
    departments: dict[str, Department] = {}
    manager_positions: dict[str, str] = {}
    for agent in agents:
        manager_positions[agent.id] = position_id_for(agent)

    positions: dict[str, Position] = {}
    occupants: dict[str, Occupant] = {}
    occupancies: dict[str, Occupancy] = {}
    for agent in agents:
        dept_id = department_id_for(agent)
        departments.setdefault(
            dept_id,
            Department(
                id=dept_id,
                organization_id=organization.id,
                name=agent.department,
                leader_position_id=None,
                mandate=[],
            ),
        )
        manager_position_id = manager_positions.get(agent.manager_id or "")
        migrated = migrate_agent_profile(
            agent,
            organization_id=organization.id,
            manager_position_id=manager_position_id,
        )
        positions[migrated.position.id] = migrated.position
        occupants[migrated.occupant.id] = migrated.occupant
        occupancies[migrated.occupancy.id] = migrated.occupancy
        if agent.manager_id is None and departments[dept_id].leader_position_id is None:
            departments[dept_id].leader_position_id = migrated.position.id

    return OrganizationState(
        organization=organization,
        departments=departments,
        positions=positions,
        occupants=occupants,
        occupancies=occupancies,
    )


def validate_organization_state(
    state: OrganizationState,
    *,
    allow_multi_position_occupants: bool | None = None,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    allow_multi = (
        bool(state.policies.get("allow_multi_position_occupants", False))
        if allow_multi_position_occupants is None
        else allow_multi_position_occupants
    )

    for department in state.departments.values():
        if department.organization_id != state.organization.id:
            errors.append(f"department {department.id} belongs to a different organization")
        if department.parent_department_id and department.parent_department_id not in state.departments:
            errors.append(f"department {department.id} references missing parent {department.parent_department_id}")
        if department.leader_position_id and department.leader_position_id not in state.positions:
            errors.append(f"department {department.id} references missing leader {department.leader_position_id}")

    for position in state.positions.values():
        if position.organization_id != state.organization.id:
            errors.append(f"position {position.id} belongs to a different organization")
        if position.department_id not in state.departments:
            errors.append(f"position {position.id} references missing department {position.department_id}")
        manager_id = position.reports_to_position_id
        if manager_id:
            manager = state.positions.get(manager_id)
            if manager is None:
                errors.append(f"position {position.id} reports to missing position {manager_id}")
            elif manager.status == "archived":
                errors.append(f"position {position.id} reports to archived position {manager_id}")

    active_primary_by_position: dict[str, str] = {}
    active_primary_by_occupant: dict[str, list[str]] = {}
    for occupancy in state.occupancies.values():
        if occupancy.position_id not in state.positions:
            errors.append(f"occupancy {occupancy.id} references missing position {occupancy.position_id}")
        if occupancy.occupant_id not in state.occupants:
            errors.append(f"occupancy {occupancy.id} references missing occupant {occupancy.occupant_id}")
        if occupancy.status != "active" or occupancy.occupancy_type != "primary":
            continue
        previous = active_primary_by_position.get(occupancy.position_id)
        if previous is not None:
            errors.append(
                f"position {occupancy.position_id} has multiple active primary occupancies: {previous}, {occupancy.id}"
            )
        active_primary_by_position[occupancy.position_id] = occupancy.id
        active_primary_by_occupant.setdefault(occupancy.occupant_id, []).append(occupancy.position_id)

    if not allow_multi:
        for occupant_id, position_ids in active_primary_by_occupant.items():
            if len(position_ids) > 1:
                errors.append(
                    f"occupant {occupant_id} fills multiple primary positions without policy permission: {position_ids}"
                )

    cycle = find_reporting_cycle(state)
    if cycle:
        errors.append(f"reporting relationship cycle detected: {' -> '.join(cycle)}")

    for position in state.positions.values():
        if position.status == "active" and not state.active_primary_occupancy_for(position.id):
            warnings.append(f"active position {position.id} has no primary occupant")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def find_reporting_cycle(state: OrganizationState) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(position_id: str, path: list[str]) -> list[str]:
        if position_id in visiting:
            index = path.index(position_id)
            return path[index:] + [position_id]
        if position_id in visited:
            return []
        visiting.add(position_id)
        manager_id = state.positions[position_id].reports_to_position_id
        if manager_id and manager_id in state.positions:
            cycle = visit(manager_id, path + [manager_id])
            if cycle:
                return cycle
        visiting.remove(position_id)
        visited.add(position_id)
        return []

    for position_id in state.positions:
        cycle = visit(position_id, [position_id])
        if cycle:
            return cycle
    return []


def replace_primary_occupant(
    state: OrganizationState,
    *,
    position_id: str,
    new_occupant: Occupant,
    handoff_artifact_id: str | None = None,
) -> OrganizationState:
    if position_id not in state.positions:
        raise ValueError(f"unknown position: {position_id}")

    now = utc_now()
    next_state = state.model_copy(deep=True)
    next_state.occupants[new_occupant.id] = new_occupant
    for occupancy_id, occupancy in list(next_state.occupancies.items()):
        if (
            occupancy.position_id == position_id
            and occupancy.occupancy_type == "primary"
            and occupancy.status == "active"
        ):
            next_state.occupancies[occupancy_id] = occupancy.model_copy(
                update={
                    "status": "ended",
                    "effective_to": now,
                    "handoff_artifact_id": handoff_artifact_id,
                }
            )
    next_state.occupancies[f"occupancy_{position_id}_{new_occupant.id}_{int(now.timestamp())}"] = Occupancy(
        id=f"occupancy_{position_id}_{new_occupant.id}_{int(now.timestamp())}",
        position_id=position_id,
        occupant_id=new_occupant.id,
        occupancy_type="primary",
        effective_from=now,
        status="active",
        handoff_artifact_id=handoff_artifact_id,
    )
    next_state.positions[position_id] = next_state.positions[position_id].model_copy(update={"updated_at": now})
    return next_state
