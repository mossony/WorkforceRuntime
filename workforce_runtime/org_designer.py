from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.config import load_runtime_config
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


DEFAULT_MANAGEMENT_MODEL = "gpt-oss-120b"
DEFAULT_WORKER_MODEL = "gpt-oss-120b"
DEFAULT_DECISION_BACKEND = "codex"
DEFAULT_MANAGEMENT_WORKER_TYPE = "codex"
DEFAULT_WORKER_WORKER_TYPE = "codex"


class OrgDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    company_name: str = "Designed Workforce"
    headcount_limit: int = Field(default=6, ge=3)
    token_budget: int = Field(default=600000, ge=0)
    management_model: str = DEFAULT_MANAGEMENT_MODEL
    worker_model: str = DEFAULT_WORKER_MODEL
    decision_backend: str = DEFAULT_DECISION_BACKEND
    management_worker_type: str = DEFAULT_MANAGEMENT_WORKER_TYPE
    worker_worker_type: str = DEFAULT_WORKER_WORKER_TYPE
    include_hr: bool = True
    max_management_depth: int = Field(default=3, ge=1, le=5)


class OrgDesigner:
    def __init__(self, *, client: OpenRouterClient | None = None) -> None:
        self.client = client

    def design(
        self,
        request: OrgDesignRequest,
        *,
        use_llm: bool = False,
        allow_fallback: bool = True,
    ) -> Organization:
        if use_llm:
            try:
                if self.client is None:
                    if request.decision_backend not in {"codex", "claude_code"}:
                        raise ValueError(f"unsupported org designer decision backend: {request.decision_backend}")
                    return self._design_with_decision_agent(request)
                return self._design_with_llm(request)
            except Exception:
                if not allow_fallback:
                    raise
        return self._fallback_design(request)

    def _design_with_llm(self, request: OrgDesignRequest) -> Organization:
        if self.client is None:
            raise RuntimeError("OpenRouter org design requires an injected client")
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
            max_tokens=min(12000, max(1600, request.headcount_limit * 450)),
            reasoning=True,
            response_format={"type": "json_object"},
        )
        data = extract_json_object(response.content)
        return organization_from_mapping(data, request=request)

    def _design_with_decision_agent(self, request: OrgDesignRequest) -> Organization:
        if request.decision_backend == "codex":
            response_text = _run_codex_org_design(request)
        elif request.decision_backend == "claude_code":
            response_text = _run_claude_org_design(request)
        else:
            raise ValueError(f"unsupported org designer decision backend: {request.decision_backend}")
        data = extract_json_object(response_text)
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
                worker_type=_management_worker_type(request),
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
                    worker_type=_management_worker_type(request),
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
                    worker_type=_management_worker_type(request),
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
                worker_type=_management_worker_type(request),
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
                worker_type=_worker_worker_type(request),
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
                    worker_type=_worker_worker_type(request),
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
        return _ensure_requested_headcount(organization, request=request, manager_id=manager_id, department=department)


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
        worker_type = _normalize_worker_type(str(item.get("worker_type") or ""), role=role, request=request)
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
    return _ensure_requested_headcount(organization, request=request)


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
      "worker_type": "{_management_worker_type(request)}" for CEO, HR, VPs, and managers; "{_worker_worker_type(request)}" for terminal workers,
      "model": string,
      "responsibilities": [string],
      "permissions": [string],
      "budget": {{"max_tokens": integer, "max_runtime_seconds": integer, "max_tool_calls": integer}}
    }}
  ]
}}

Constraints:
- Return exactly {request.headcount_limit} agents unless impossible.
- Company headcount_limit must be {request.headcount_limit}.
- Use {request.management_model} for CEO, HR, VPs, and managers.
- Use {request.worker_model} for terminal workers.
- Use worker_type "{_management_worker_type(request)}" for every decision-making agent.
- Use worker_type "{_worker_worker_type(request)}" for terminal workers.
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
        _finalize_agent(organization.company, agent) for agent in organization.agents
    ]
    return organization.model_copy(update={"agents": agents})


def _finalize_agent(company: Company, agent: AgentProfile) -> AgentProfile:
    update: dict[str, Any] = {}
    # Every agent must be able to record evidence, even management agents that
    # occasionally execute work directly (third-party workers do not reliably
    # delegate). submit_artifact is an evidence capability, not a governance
    # privilege like delegate/approve_budget/hire.
    permissions = list(agent.permissions)
    original = list(permissions)
    is_root = agent.manager_id is None
    if SUBMIT_ARTIFACT not in permissions:
        permissions.append(SUBMIT_ARTIFACT)
    # The root agent reports upward to the human, not to a manager agent, so it
    # uses report_to_human() instead of report(). Giving it report() would make
    # its completion report target the non-existent "human" agent and break the
    # manager-review flow. Every non-root agent reports to its manager.
    if is_root:
        if REPORT_TO_HUMAN not in permissions:
            permissions.append(REPORT_TO_HUMAN)
        permissions = [p for p in permissions if p != REPORT]
    elif REPORT not in permissions:
        permissions.append(REPORT)
    if permissions != original:
        update["permissions"] = permissions
    if not agent.system_prompt.strip():
        update["system_prompt"] = generate_system_prompt(company, agent)
    return agent.model_copy(update=update) if update else agent


def _ensure_requested_headcount(
    organization: Organization,
    *,
    request: OrgDesignRequest,
    manager_id: str | None = None,
    department: str | None = None,
) -> Organization:
    target = request.headcount_limit
    agents = list(organization.agents[:target])
    if len(agents) >= target:
        company = organization.company.model_copy(update={"headcount_limit": target})
        return _with_system_prompts(organization.model_copy(update={"company": company, "agents": agents}))

    known_ids = {agent.id for agent in agents}
    parent_id = manager_id or _default_worker_manager_id(agents)
    role_department = department or _default_worker_department(agents, request=request)
    goal_kind = _goal_kind(request.goal)
    role = "Research Analyst" if goal_kind == "research" else "Implementation Specialist"
    responsibility = (
        "Investigate an assigned research slice and report evidence"
        if goal_kind == "research"
        else "Implement an assigned work slice and report evidence"
    )
    ratio = min(0.08, max(0.01, 0.45 / max(target, 1)))
    next_index = 1
    while len(agents) < target:
        agent_id = f"worker_{next_index:02d}"
        next_index += 1
        if agent_id in known_ids:
            continue
        known_ids.add(agent_id)
        agents.append(
            AgentProfile(
                id=agent_id,
                name=f"{role} {next_index - 1} Agent",
                role=role,
                department=role_department,
                manager_id=parent_id,
                worker_type=_worker_worker_type(request),
                model=request.worker_model,
                responsibilities=[responsibility, "Submit artifacts and structured reports"],
                permissions=[READ_REPO, SUBMIT_ARTIFACT, REPORT],
                budget=_budget(request.token_budget, ratio),
            )
        )

    company = organization.company.model_copy(update={"headcount_limit": target})
    return _with_system_prompts(organization.model_copy(update={"company": company, "agents": agents}))


def _default_worker_manager_id(agents: list[AgentProfile]) -> str | None:
    for agent in reversed(agents):
        if DELEGATE_TASK in agent.permissions and agent.manager_id is not None:
            return agent.id
    for agent in agents:
        if agent.manager_id is None:
            return agent.id
    return agents[0].id if agents else None


def _default_worker_department(agents: list[AgentProfile], *, request: OrgDesignRequest) -> str:
    for agent in reversed(agents):
        if agent.department and agent.department != "Executive":
            return agent.department
    return "Research" if _goal_kind(request.goal) == "research" else "Engineering"


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


def _run_codex_org_design(request: OrgDesignRequest) -> str:
    config = load_runtime_config()
    codex_config = config.get("workers", {}).get("codex", {})
    workspace_root = Path(str(config.get("runtime", {}).get("workspace_root") or ".workforce_runtime"))
    run_dir = workspace_root / "org_design" / f"codex_{uuid4().hex[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = _decision_agent_org_design_prompt(request)
    prompt_path = run_dir / "prompt.txt"
    response_path = run_dir / "organization-response.txt"
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"
    prompt_path.write_text(prompt)
    command = [
        str(codex_config.get("executable") or "codex"),
        "--profile",
        str(codex_config.get("profile") or "workforce-openrouter"),
        "-m",
        request.management_model,
        "-a",
        str(codex_config.get("approval_policy") or "never"),
        "-s",
        str(codex_config.get("sandbox_mode") or "workspace-write"),
        "-C",
        str(Path.cwd()),
        "exec",
        "--output-last-message",
        str(response_path),
        prompt,
    ]
    result = subprocess.run(
        command,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=_optional_timeout_seconds(codex_config.get("timeout_seconds"), default=300),
        check=False,
    )
    stdout_path.write_text(result.stdout)
    stderr_path.write_text(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Codex org design failed with exit code {result.returncode}: {result.stderr[-1000:]}")
    if response_path.exists() and response_path.read_text().strip():
        return response_path.read_text()
    return result.stdout


def _run_claude_org_design(request: OrgDesignRequest) -> str:
    config = load_runtime_config()
    claude_config = config.get("workers", {}).get("claude_code", {})
    workspace_root = Path(str(config.get("runtime", {}).get("workspace_root") or ".workforce_runtime"))
    run_dir = workspace_root / "org_design" / f"claude_{uuid4().hex[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = _decision_agent_org_design_prompt(request)
    prompt_path = run_dir / "prompt.txt"
    stdout_path = run_dir / "stdout.json"
    stderr_path = run_dir / "stderr.txt"
    prompt_path.write_text(prompt)
    command = [
        str(claude_config.get("executable") or "claude"),
        "-p",
        prompt,
        "--output-format",
        "json",
    ]
    result = subprocess.run(
        command,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=_optional_timeout_seconds(claude_config.get("timeout_seconds"), default=300),
        check=False,
    )
    stdout_path.write_text(result.stdout)
    stderr_path.write_text(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Claude org design failed with exit code {result.returncode}: {result.stderr[-1000:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout
    return str(payload.get("result") or payload.get("content") or payload.get("text") or result.stdout)


def _decision_agent_org_design_prompt(request: OrgDesignRequest) -> str:
    return (
        "You are a Workforce Runtime organization design agent running inside Codex or Claude Code. "
        "Design the organization by returning only a JSON object. Do not include Markdown fences, prose, or commentary.\n\n"
        f"{_org_design_prompt(request)}"
    )


def _optional_timeout_seconds(value: object, *, default: int) -> int:
    if value in {None, ""}:
        return default
    return int(value)


def _management_worker_type(request: OrgDesignRequest) -> str:
    return _normalized_runtime_worker_type(request.management_worker_type, default=DEFAULT_MANAGEMENT_WORKER_TYPE)


def _worker_worker_type(request: OrgDesignRequest) -> str:
    return _normalized_runtime_worker_type(request.worker_worker_type, default=DEFAULT_WORKER_WORKER_TYPE)


def _normalized_runtime_worker_type(value: str, *, default: str) -> str:
    normalized = (value or default).strip()
    if normalized in {"openrouter_manager", "openrouter_worker"}:
        return default
    return normalized or default


def _normalize_worker_type(value: str, *, role: str, request: OrgDesignRequest) -> str:
    lowered_value = value.strip().lower()
    if lowered_value in {"", "openrouter_manager", "openrouter_worker"}:
        return _worker_type_for_role(role, request)
    return value.strip()


def _worker_type_for_role(role: str, request: OrgDesignRequest) -> str:
    lowered = role.lower()
    if any(term in lowered for term in ("ceo", "vp", "manager", "lead", "hr")):
        return _management_worker_type(request)
    return _worker_worker_type(request)


def _model_for_role(role: str, request: OrgDesignRequest) -> str:
    lowered = role.lower()
    return request.management_model if any(term in lowered for term in ("ceo", "vp", "manager", "lead", "hr")) else request.worker_model


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
