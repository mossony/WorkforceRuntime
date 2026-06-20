from __future__ import annotations

from workforce_runtime.config import format_model_context_note
from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.core.organization import Company
from workforce_runtime.core.permissions import HIRE_AGENT, REPORT_TO_HUMAN


def generate_system_prompt(company: Company, agent: AgentProfile) -> str:
    """Generate the default operating prompt for an org agent."""
    reporting_line = (
        f"Your direct manager is {agent.manager_id}."
        if agent.manager_id
        else "You report to the human operator."
    )
    responsibilities = _bullet_list(agent.responsibilities)
    permissions = _bullet_list(agent.permissions)
    role_guidance = _role_guidance(agent)

    return "\n".join(
        [
            f"You are {agent.name}, the {agent.role} in {company.name}.",
            f"Assigned model: {agent.model or 'runtime default'}.",
            format_model_context_note(agent.model),
            f"Company mission: {company.mission or 'not specified'}.",
            reporting_line,
            "",
            "Responsibilities:",
            responsibilities,
            "",
            "Allowed permissions:",
            permissions,
            "",
            role_guidance,
            "",
            "Operating rules:",
            "- Communicate through Workforce Runtime MCP tools.",
            "- Use assign() only for agents under your reporting line.",
            "- Use discuss() for peer or cross-functional messages.",
            "- Use report() to send completion status to your direct manager.",
            _human_report_rule(agent),
            "- Use get_agent_profiles() before assignment decisions when prior agent experience could affect routing.",
            "- Use get_task_dossier() to pull requirements, division of work, task documents, reports, and artifacts when you need context.",
            "- Use upsert_task_doc() to preserve requirements, decisions, notes, risks, or division-of-work updates for future agents.",
            "- Use update_agent_profile() after meaningful work to keep your reusable personal profile current.",
            "- Use request_tool() when a repeated missing tool makes work unnecessarily manual or error-prone.",
            "- Do not claim completion without evidence, artifacts, or a clear no-tools report.",
        ]
    )


def _role_guidance(agent: AgentProfile) -> str:
    role = agent.role.lower()
    if agent.manager_id is None or "ceo" in role:
        return (
            "CEO guidance: translate company goals into executive priorities, delegate to VPs "
            "or HR, watch budget and headcount, and escalate final decisions to the human operator."
        )
    if HIRE_AGENT in agent.permissions or "hr" in role:
        return (
            "HR guidance: create workers or managers only when headcount and token budget allow it, "
            "assign every hire to a manager, and keep hiring decisions auditable."
        )
    if "vp" in role or "manager" in role or "lead" in role:
        return (
            "Manager guidance: break objectives into task contracts, assign work to subordinate "
            "agents, review reports, manage risks, and report upward."
        )
    return (
        "Worker guidance: execute assigned tasks within budget and permissions, ask for help when "
        "blocked, submit artifacts when tools are used, and report to your direct manager."
    )


def _human_report_rule(agent: AgentProfile) -> str:
    if REPORT_TO_HUMAN in agent.permissions:
        return "- Use report_to_human() for final CEO-level updates that the human operator must see clearly."
    return "- Do not use report_to_human(); it is reserved for the CEO or explicitly authorized top-level agent."


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "- None specified."
    return "\n".join(f"- {item}" for item in items)
