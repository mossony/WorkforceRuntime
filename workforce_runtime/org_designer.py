from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core import AgentProfile, Budget, Company, Organization, generate_system_prompt
from workforce_runtime.core.permissions import (
    APPROVE_BUDGET,
    DELEGATE_TASK,
    HIRE_AGENT,
    READ_REPO,
    REPORT,
    REPORT_TO_HUMAN,
    REQUEST_BUDGET,
    SUBMIT_ARTIFACT,
)
from workforce_runtime.llm import OpenRouterClient, extract_json_object


DEFAULT_MANAGEMENT_MODEL = "openai/gpt-oss-120b:free"
DEFAULT_WORKER_MODEL = "poolside/laguna-xs.2:free"


class OrgDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    company_name: str = "Designed Workforce"
    headcount_limit: int = Field(default=6, ge=3)
    token_budget: int = Field(default=600000, ge=0)
    management_model: str = DEFAULT_MANAGEMENT_MODEL
    worker_model: str = DEFAULT_WORKER_MODEL
    include_hr: bool = True
    max_management_depth: int = Field(default=3, ge=1, le=5)


class OrgDesigner:
    def __init__(self, *, client: OpenRouterClient | None = None) -> None:
        self.client = client or OpenRouterClient()

    def design(
        self,
        request: OrgDesignRequest,
        *,
        use_llm: bool = False,
        allow_fallback: bool = True,
    ) -> Organization:
        if use_llm:
            try:
                return self._design_with_llm(request)
            except Exception:
                if not allow_fallback:
                    raise
        return self._fallback_design(request)

    def _design_with_llm(self, request: OrgDesignRequest) -> Organization:
        response = self.client.chat(
            model=request.management_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You design small AI company org charts for Workforce Runtime. "
                        "Return only JSON that validates against the requested schema. "
                        "Managers must manage only their descendants. Leaf workers execute tasks. "
                        "Keep the organization lean and auditable."
                    ),
                },
                {
                    "role": "user",
                    "content": _org_design_prompt(request),
                },
            ],
            temperature=0.1,
            max_tokens=1600,
            reasoning=True,
            response_format={"type": "json_object"},
        )
        data = extract_json_object(response.content)
        return organization_from_mapping(data, request=request)

    def _fallback_design(self, request: OrgDesignRequest) -> Organization:
        goal_kind = _goal_kind(request.goal)
        department = "Research" if goal_kind == "research" else "Engineering"
        vp_role = "VP Research" if goal_kind == "research" else "VP Engineering"
        manager_role = "Research Manager" if goal_kind == "research" else "Engineering Manager"
        worker_role = "Research Worker" if goal_kind == "research" else "Implementation Worker"
        worker_responsibility = (
            "Fetch, inspect, and summarize source material with evidence"
            if goal_kind == "research"
            else "Implement scoped work and submit evidence"
        )

        agents: list[AgentProfile] = [
            AgentProfile(
                id="ceo",
                name="CEO Agent",
                role="CEO",
                department="Executive",
                worker_type="openrouter_manager",
                model=request.management_model,
                responsibilities=[
                    "Turn the human goal into executive priorities",
                    "Delegate work while preserving budget and acceptance criteria",
                ],
                permissions=[DELEGATE_TASK, APPROVE_BUDGET, HIRE_AGENT, REPORT, REPORT_TO_HUMAN],
                budget=_budget(request.token_budget, 0.22),
            )
        ]
        if request.include_hr and request.headcount_limit >= 4:
            agents.append(
                AgentProfile(
                    id="hr_manager",
                    name="HR Manager Agent",
                    role="HR Manager",
                    department="People",
                    manager_id="ceo",
                    worker_type="openrouter_manager",
                    model=request.management_model,
                    responsibilities=[
                        "Check headcount and token budget before hiring",
                        "Create workers or managers when budget allows",
                    ],
                    permissions=[HIRE_AGENT, REPORT, REQUEST_BUDGET],
                    budget=_budget(request.token_budget, 0.1),
                )
            )

        include_vp = request.headcount_limit >= 5 and request.max_management_depth >= 2
        manager_id = "delivery_manager"
        if include_vp:
            agents.append(
                AgentProfile(
                    id="vp_delivery",
                    name=f"{vp_role} Agent",
                    role=vp_role,
                    department=department,
                    manager_id="ceo",
                    worker_type="openrouter_manager",
                    model=request.management_model,
                    responsibilities=[
                        "Convert executive goal into manager-ready work",
                        "Control scope and risks for the execution team",
                    ],
                    permissions=[DELEGATE_TASK, REPORT, REQUEST_BUDGET],
                    budget=_budget(request.token_budget, 0.18),
                )
            )
            manager_parent = "vp_delivery"
        else:
            manager_parent = "ceo"

        agents.append(
            AgentProfile(
                id=manager_id,
                name=f"{manager_role} Agent",
                role=manager_role,
                department=department,
                manager_id=manager_parent,
                worker_type="openrouter_manager",
                model=request.management_model,
                responsibilities=[
                    "Create concrete worker task contracts",
                    "Check progress, review reports, and escalate blockers",
                ],
                permissions=[DELEGATE_TASK, REPORT, REQUEST_BUDGET],
                budget=_budget(request.token_budget, 0.18),
            )
        )
        agents.append(
            AgentProfile(
                id="primary_worker",
                name=f"{worker_role} Agent",
                role=worker_role,
                department=department,
                manager_id=manager_id,
                worker_type="openrouter_worker",
                model=request.worker_model,
                responsibilities=[worker_responsibility, "Submit artifacts and structured reports"],
                permissions=[READ_REPO, SUBMIT_ARTIFACT, REPORT],
                budget=_budget(request.token_budget, 0.22),
            )
        )
        if request.headcount_limit >= len(agents) + 1:
            agents.append(
                AgentProfile(
                    id="peer_reviewer",
                    name="Peer Reviewer Agent",
                    role="Peer Reviewer",
                    department=department,
                    manager_id=manager_id,
                    worker_type="openrouter_worker",
                    model=request.worker_model,
                    responsibilities=["Receive peer discussion and sanity-check evidence"],
                    permissions=[READ_REPO, REPORT],
                    budget=_budget(request.token_budget, 0.1),
                )
            )

        organization = Organization(
            company=Company(
                name=request.company_name,
                mission=request.goal,
                headcount_limit=request.headcount_limit,
                token_budget=request.token_budget,
            ),
            agents=agents[: request.headcount_limit],
        )
        return _with_system_prompts(organization)


def organization_from_mapping(data: dict[str, Any], *, request: OrgDesignRequest) -> Organization:
    company_input = data.get("company") if isinstance(data.get("company"), dict) else {}
    company = Company(
        name=str(company_input.get("name") or request.company_name),
        mission=str(company_input.get("mission") or request.goal),
        headcount_limit=min(int(company_input.get("headcount_limit") or request.headcount_limit), request.headcount_limit),
        token_budget=int(company_input.get("token_budget") or request.token_budget),
    )
    agents_input = data.get("agents") or []
    if not isinstance(agents_input, list) or len(agents_input) < 3:
        raise ValueError("org designer returned fewer than three agents")

    agents: list[AgentProfile] = []
    used_ids: set[str] = set()
    for index, item in enumerate(agents_input[: request.headcount_limit]):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or ("CEO" if index == 0 else "Worker"))
        agent_id = _unique_id(str(item.get("id") or role), used_ids)
        used_ids.add(agent_id)
        manager_id = item.get("manager_id")
        manager = str(manager_id) if manager_id else None
        worker_type = str(item.get("worker_type") or _worker_type_for_role(role))
        model = str(item.get("model") or _model_for_role(role, request))
        agents.append(
            AgentProfile(
                id=agent_id,
                name=str(item.get("name") or f"{role} Agent"),
                role=role,
                department=str(item.get("department") or "General"),
                manager_id=manager,
                worker_type=worker_type,
                model=model,
                responsibilities=[str(value) for value in item.get("responsibilities") or []],
                permissions=_permissions_for_item(item, role),
                budget=Budget.model_validate(item.get("budget") or _budget(request.token_budget, 0.15).model_dump()),
                system_prompt=str(item.get("system_prompt") or ""),
            )
        )

    if not any(agent.manager_id is None for agent in agents):
        agents[0] = agents[0].model_copy(update={"manager_id": None, "role": "CEO"})

    known_ids = {agent.id for agent in agents}
    repaired: list[AgentProfile] = []
    for index, agent in enumerate(agents):
        if agent.manager_id is not None and agent.manager_id not in known_ids:
            agent = agent.model_copy(update={"manager_id": agents[0].id if index else None})
        agent = _ensure_default_permissions(agent)
        repaired.append(agent)

    organization = Organization(company=company, agents=repaired)
    return _with_system_prompts(organization)


def organization_to_yaml(organization: Organization) -> str:
    return yaml.safe_dump(organization.model_dump(mode="json"), sort_keys=False)


def _org_design_prompt(request: OrgDesignRequest) -> str:
    return f"""
Design a Workforce Runtime organization for this goal:
{request.goal}

Return JSON only:
{{
  "company": {{
    "name": string,
    "mission": string,
    "headcount_limit": integer,
    "token_budget": integer
  }},
  "agents": [
    {{
      "id": "stable_snake_case_id",
      "name": string,
      "role": string,
      "department": string,
      "manager_id": string or null,
      "worker_type": "openrouter_manager" or "openrouter_worker",
      "model": string,
      "responsibilities": [string],
      "permissions": [string],
      "budget": {{"max_tokens": integer, "max_runtime_seconds": integer, "max_tool_calls": integer}}
    }}
  ]
}}

Constraints:
- Headcount must be between 3 and {request.headcount_limit}.
- Use {request.management_model} for CEO, HR, VPs, and managers.
- Use {request.worker_model} for terminal workers.
- Include exactly one root CEO with manager_id null.
- CEO needs delegate_task, approve_budget, hire_agent, report, and report_to_human.
- Include HR if useful for headcount or token budget decisions.
- Managers that assign work need permissions: delegate_task, report, request_budget.
- HR needs hire_agent and report.
- Workers need read_repo, submit_artifact, report.
- Keep depth <= {request.max_management_depth + 1} including CEO.
""".strip()


def _with_system_prompts(organization: Organization) -> Organization:
    agents = [
        agent
        if agent.system_prompt.strip()
        else agent.model_copy(update={"system_prompt": generate_system_prompt(organization.company, agent)})
        for agent in organization.agents
    ]
    return organization.model_copy(update={"agents": agents})


def _goal_kind(goal: str) -> str:
    normalized = goal.lower()
    if any(term in normalized for term in ("research", "rfc", "web", "internet", "paper", "report")):
        return "research"
    return "engineering"


def _budget(token_budget: int, ratio: float) -> Budget:
    tokens = int(token_budget * ratio) if token_budget else 0
    return Budget(max_tokens=tokens, max_runtime_seconds=3600, max_tool_calls=80)


def _unique_id(value: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "agent"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _worker_type_for_role(role: str) -> str:
    lowered = role.lower()
    if any(term in lowered for term in ("ceo", "vp", "manager", "lead", "hr")):
        return "openrouter_manager"
    return "openrouter_worker"


def _model_for_role(role: str, request: OrgDesignRequest) -> str:
    return request.management_model if _worker_type_for_role(role) == "openrouter_manager" else request.worker_model


def _permissions_for_item(item: dict[str, Any], role: str) -> list[str]:
    permissions = [str(value) for value in item.get("permissions") or []]
    if permissions:
        return permissions
    lowered = role.lower()
    if "hr" in lowered:
        return [HIRE_AGENT, REPORT, REQUEST_BUDGET]
    if any(term in lowered for term in ("ceo", "vp", "manager", "lead")):
        base = [DELEGATE_TASK, REPORT, REQUEST_BUDGET]
        if "ceo" in lowered:
            base.extend([APPROVE_BUDGET, HIRE_AGENT, REPORT_TO_HUMAN])
        return base
    return [READ_REPO, SUBMIT_ARTIFACT, REPORT]


def _ensure_default_permissions(agent: AgentProfile) -> AgentProfile:
    permissions = list(agent.permissions)
    lowered = agent.role.lower()
    if agent.manager_id is None or "ceo" in lowered or "chief executive" in lowered:
        for permission in (DELEGATE_TASK, REPORT, APPROVE_BUDGET, HIRE_AGENT, REPORT_TO_HUMAN):
            if permission not in permissions:
                permissions.append(permission)
    return agent.model_copy(update={"permissions": permissions})
