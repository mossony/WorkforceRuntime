from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from workforce_runtime.core import Budget
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def send_request(process: subprocess.Popen[str], message: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def test_org_loader_generates_role_specific_system_prompts(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        ceo = runtime.get_agent("ceo")
        hr = runtime.get_agent("hr_manager")
        worker = runtime.get_agent("codex_worker")

    assert ceo is not None
    assert hr is not None
    assert worker is not None
    assert "CEO guidance" in ceo.system_prompt
    assert "Model context window: unknown" in ceo.system_prompt
    assert "HR guidance" in hr.system_prompt
    assert "Worker guidance" in worker.system_prompt
    assert "Model context window: unknown" in worker.system_prompt
    assert "Use report() to send completion status to your direct manager." in worker.system_prompt


def test_mcp_assign_discuss_and_report_to_direct_manager(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

    process = subprocess.Popen(
        [sys.executable, "-m", "workforce_runtime", "--db", str(db_path), "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        tools = send_request(process, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        assert {"assign", "check_progress", "discuss", "hire", "update_system_prompt"} <= tool_names

        assigned = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "assign",
                    "arguments": {
                        "from_agent_id": "engineering_manager",
                        "to_agent_id": "codex_worker",
                        "title": "Fix parser",
                        "message": "Fix the parser bug and report evidence.",
                        "acceptance_criteria": ["pytest passes"],
                    },
                },
            },
        )
        assert assigned["result"]["structuredContent"]["task_id"] == "task_001"

        discussed = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "discuss",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "to_agent_id": "claude_worker",
                        "task_id": "task_001",
                        "message": "Can you sanity-check the parser edge cases?",
                    },
                },
            },
        )
        assert discussed["result"]["structuredContent"]["to_agent_id"] == "claude_worker"

        checked = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "check_progress",
                    "arguments": {
                        "from_agent_id": "engineering_manager",
                        "target_agent_id": "codex_worker",
                        "task_id": "task_001",
                        "message": "Show current task status.",
                    },
                },
            },
        )
        assert checked["result"]["structuredContent"]["ok"] is True
        assert checked["result"]["structuredContent"]["active_tasks"][0]["task_id"] == "task_001"

        reported = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "report",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "task_id": "task_001",
                        "summary": "Parser fixed.",
                        "status": "completed",
                        "confidence": 0.9,
                    },
                },
            },
        )
        assert reported["result"]["structuredContent"]["to_agent_id"] == "engineering_manager"
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    with WorkforceRuntime(db_path) as runtime:
        task = runtime.require_task("task_001")
        report = runtime.store.list_reports_by_task("task_001")[0]
        discussion = next(event for event in runtime.store.list_events() if event.event_type == "discussion_message")
        event_types = [event.event_type for event in runtime.store.list_events()]

    assert task.assigned_by == "engineering_manager"
    assert report.to_agent_id == "engineering_manager"
    assert discussion.payload["message"] == "Can you sanity-check the parser edge cases?"
    assert "progress_check_requested" in event_types


def test_assign_requires_reporting_line_even_with_delegate_permission(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        task = runtime.create_task(
            title="VP delegates implementation",
            objective="VP can delegate within their reporting tree.",
            assign_to="codex_worker",
            assigned_by="vp_engineering",
        )
        assert task.assigned_to == "codex_worker"

        with pytest.raises(PermissionError):
            runtime.create_task(
                title="HR cannot assign engineering work",
                objective="HR has no reporting line to Codex.",
                assign_to="codex_worker",
                assigned_by="hr_manager",
            )


def test_hr_hire_respects_company_budget_and_generates_prompt(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        hired = runtime.hire_agent(
            requested_by="hr_manager",
            agent_id="qa_worker",
            name="QA Worker",
            role="QA Engineer",
            department="Engineering",
            manager_id="engineering_manager",
            worker_type="generic_cli",
            responsibilities=["Run regression tests"],
            permissions=["read_repo", "run_tests", "report"],
            budget=Budget(max_tokens=10000, max_runtime_seconds=1200, max_tool_calls=20),
        )

        assert hired.manager_id == "engineering_manager"
        assert "Worker guidance" in hired.system_prompt
        assert "Model context window: unknown" in hired.system_prompt

        with pytest.raises(ValueError, match="token budget"):
            runtime.hire_agent(
                requested_by="hr_manager",
                agent_id="expensive_worker",
                name="Expensive Worker",
                role="Software Engineer",
                department="Engineering",
                manager_id="engineering_manager",
                worker_type="codex",
                budget=Budget(max_tokens=200000),
            )


def test_manager_can_update_subordinate_system_prompt(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        updated = runtime.update_system_prompt(
            actor_id="engineering_manager",
            target_agent_id="codex_worker",
            system_prompt="Custom worker prompt.",
        )
        assert updated.system_prompt == "Custom worker prompt."

        with pytest.raises(PermissionError):
            runtime.update_system_prompt(
                actor_id="hr_manager",
                target_agent_id="codex_worker",
                system_prompt="Bad prompt edit.",
            )
