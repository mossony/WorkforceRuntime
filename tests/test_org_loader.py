from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from workforce_runtime.storage import load_org_from_yaml


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_load_org_from_yaml() -> None:
    organization = load_org_from_yaml(EXAMPLE_ORG)

    assert organization.company.name == "Demo Workforce"
    assert organization.find_agent("ceo") is not None
    assert organization.get_manager("codex_worker").id == "engineering_manager"
    assert [agent.id for agent in organization.get_direct_reports("engineering_manager")] == [
        "codex_worker",
        "claude_worker",
    ]
    assert [agent.id for agent in organization.get_reporting_chain("codex_worker")] == [
        "engineering_manager",
        "vp_engineering",
        "ceo",
    ]


def test_org_chart_text_is_readable() -> None:
    organization = load_org_from_yaml(EXAMPLE_ORG)

    chart = organization.to_org_chart_text()

    assert "Demo Workforce" in chart
    assert "CEO Agent (CEO, Executive) [idle]" in chart
    assert "VP Engineering Agent (VP Engineering, Engineering) [idle]" in chart
    assert "Codex Worker (Software Engineer, Engineering) [idle]" in chart


def test_org_print_cli_end_to_end() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "workforce_runtime", "org", "print", str(EXAMPLE_ORG)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Demo Workforce" in result.stdout
    assert "Organization:" in result.stdout
    assert "CEO Agent" in result.stdout
    assert "Codex Worker" in result.stdout


def test_load_org_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    org_path = tmp_path / "org.yaml"
    org_path.write_text("- not\n- a\n- mapping\n")

    with pytest.raises(ValueError):
        load_org_from_yaml(org_path)
