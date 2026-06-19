from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from workforce_runtime.core.agent_profile import AgentProfile


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    mission: str = ""
    headcount_limit: int = Field(default=0, ge=0)
    token_budget: int = Field(default=0, ge=0)


class Organization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Company
    agents: list[AgentProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reporting_lines(self) -> Organization:
        ids = [agent.id for agent in self.agents]
        duplicate_ids = {agent_id for agent_id in ids if ids.count(agent_id) > 1}
        if duplicate_ids:
            duplicates = ", ".join(sorted(duplicate_ids))
            raise ValueError(f"duplicate agent ids: {duplicates}")

        agent_ids = set(ids)
        missing_managers = sorted(
            {
                agent.manager_id
                for agent in self.agents
                if agent.manager_id is not None and agent.manager_id not in agent_ids
            }
        )
        if missing_managers:
            missing = ", ".join(missing_managers)
            raise ValueError(f"manager ids not found: {missing}")

        for agent in self.agents:
            seen = {agent.id}
            current = agent
            while current.manager_id is not None:
                if current.manager_id in seen:
                    raise ValueError(f"reporting cycle detected at agent: {agent.id}")
                seen.add(current.manager_id)
                current = self._require_agent_unvalidated(current.manager_id)

        return self

    def _require_agent_unvalidated(self, agent_id: str) -> AgentProfile:
        agent = next((agent for agent in self.agents if agent.id == agent_id), None)
        if agent is None:
            raise KeyError(f"agent not found: {agent_id}")
        return agent

    def find_agent(self, agent_id: str) -> AgentProfile | None:
        return next((agent for agent in self.agents if agent.id == agent_id), None)

    def require_agent(self, agent_id: str) -> AgentProfile:
        agent = self.find_agent(agent_id)
        if agent is None:
            raise KeyError(f"agent not found: {agent_id}")
        return agent

    def get_manager(self, agent_id: str) -> AgentProfile | None:
        agent = self.require_agent(agent_id)
        if agent.manager_id is None:
            return None
        return self.require_agent(agent.manager_id)

    def get_direct_reports(self, agent_id: str) -> list[AgentProfile]:
        self.require_agent(agent_id)
        return [agent for agent in self.agents if agent.manager_id == agent_id]

    def get_reporting_chain(self, agent_id: str) -> list[AgentProfile]:
        chain: list[AgentProfile] = []
        current = self.require_agent(agent_id)

        while current.manager_id is not None:
            manager = self.require_agent(current.manager_id)
            chain.append(manager)
            current = manager

        return chain

    def get_department_agents(self, department: str) -> list[AgentProfile]:
        return [agent for agent in self.agents if agent.department == department]

    def to_org_chart_text(self) -> str:
        lines = [
            self.company.name,
            f"Mission: {self.company.mission}" if self.company.mission else "Mission: ",
            "",
            "Organization:",
        ]

        roots = [agent for agent in self.agents if agent.manager_id is None]
        for root in roots:
            lines.extend(self._format_agent_tree(root, depth=1))

        return "\n".join(lines)

    def _format_agent_tree(self, agent: AgentProfile, *, depth: int) -> list[str]:
        indent = "  " * depth
        status = f" [{agent.status}]"
        lines = [f"{indent}{agent.name} ({agent.role}, {agent.department}){status}"]

        for report in self.get_direct_reports(agent.id):
            lines.extend(self._format_agent_tree(report, depth=depth + 1))

        return lines
