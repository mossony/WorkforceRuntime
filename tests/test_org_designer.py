from __future__ import annotations

import json

from workforce_runtime.llm import OpenRouterResponse
from workforce_runtime.org_designer import OrgDesigner, OrgDesignRequest


class FakeOrgClient:
    def is_configured(self) -> bool:
        return True

    def chat(self, **_kwargs: object) -> OpenRouterResponse:
        return OpenRouterResponse(
            content=json.dumps(
                {
                    "company": {
                        "name": "LLM Designed Workforce",
                        "mission": "Investigate a public web source.",
                        "headcount_limit": 4,
                        "token_budget": 200000,
                    },
                    "agents": [
                        {
                            "id": "ceo",
                            "name": "CEO Agent",
                            "role": "CEO",
                            "department": "Executive",
                            "manager_id": None,
                            "worker_type": "openrouter_manager",
                            "model": "openai/gpt-oss-120b:free",
                            "responsibilities": ["Own goal"],
                            "permissions": ["delegate_task", "report", "hire_agent"],
                            "budget": {"max_tokens": 50000, "max_runtime_seconds": 3600, "max_tool_calls": 40},
                        },
                        {
                            "id": "research_manager",
                            "name": "Research Manager Agent",
                            "role": "Research Manager",
                            "department": "Research",
                            "manager_id": "ceo",
                            "worker_type": "openrouter_manager",
                            "model": "openai/gpt-oss-120b:free",
                            "responsibilities": ["Assign work"],
                            "permissions": ["delegate_task", "report", "request_budget"],
                            "budget": {"max_tokens": 50000, "max_runtime_seconds": 3600, "max_tool_calls": 40},
                        },
                        {
                            "id": "primary_worker",
                            "name": "Research Worker Agent",
                            "role": "Research Worker",
                            "department": "Research",
                            "manager_id": "research_manager",
                            "worker_type": "openrouter_worker",
                            "model": "poolside/laguna-m.1:free",
                            "responsibilities": ["Submit artifacts"],
                            "permissions": ["read_repo", "submit_artifact", "report"],
                            "budget": {"max_tokens": 50000, "max_runtime_seconds": 3600, "max_tool_calls": 40},
                        },
                    ],
                }
            ),
            raw={},
            usage={},
        )


def test_org_designer_fallback_creates_budgeted_management_chain() -> None:
    organization = OrgDesigner().design(
        OrgDesignRequest(
            goal="Research a public RFC and produce an evidence-backed summary.",
            headcount_limit=6,
            token_budget=600000,
        )
    )

    agents = {agent.id: agent for agent in organization.agents}
    assert organization.company.mission.startswith("Research a public RFC")
    assert agents["ceo"].model == "openai/gpt-oss-120b:free"
    assert "report_to_human" in agents["ceo"].permissions
    assert agents["primary_worker"].model == "poolside/laguna-xs.2:free"
    assert "submit_artifact" in agents["primary_worker"].permissions
    assert agents["primary_worker"].system_prompt


def test_org_designer_can_use_llm_json_response() -> None:
    organization = OrgDesigner(client=FakeOrgClient()).design(
        OrgDesignRequest(goal="Investigate a public web source.", headcount_limit=4, token_budget=200000),
        use_llm=True,
    )

    assert organization.company.name == "LLM Designed Workforce"
    assert [agent.id for agent in organization.agents] == ["ceo", "research_manager", "primary_worker"]
    assert "report_to_human" in organization.require_agent("ceo").permissions
    assert organization.require_agent("primary_worker").manager_id == "research_manager"
