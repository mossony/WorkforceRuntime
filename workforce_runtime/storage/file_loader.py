from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from workforce_runtime.core.organization import Organization
from workforce_runtime.core.system_prompt import generate_system_prompt


def load_org_from_yaml(path: str | Path) -> Organization:
    yaml_path = Path(path)
    data = yaml.safe_load(yaml_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"organization YAML must contain a mapping: {yaml_path}")
    organization = Organization.model_validate(data)
    agents = [
        agent
        if agent.system_prompt.strip()
        else agent.model_copy(update={"system_prompt": generate_system_prompt(organization.company, agent)})
        for agent in organization.agents
    ]
    return organization.model_copy(update={"agents": agents})
