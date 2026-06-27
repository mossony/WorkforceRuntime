from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def send_request(process: subprocess.Popen[str], message: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def test_mcp_server_report_tool_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Fix parser test",
            objective="Fix the parser and report evidence.",
            assign_to="codex_worker",
        )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "mcp",
            "serve",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        initialize = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        assert initialize["result"]["serverInfo"]["name"] == "workforce-runtime"

        tools = send_request(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        assert "report" in tool_names
        assert "report_to_human" in tool_names
        assert "review_report" in tool_names
        assert "update_agent_profile" in tool_names
        assert "get_agent_profiles" in tool_names

        org_context = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 31,
                "method": "tools/call",
                "params": {"name": "get_org_context", "arguments": {}},
            },
        )
        agent_context = org_context["result"]["structuredContent"]["agents"][0]
        assert "model_capabilities" in agent_context

        updated_profile = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 32,
                "method": "tools/call",
                "params": {
                    "name": "update_agent_profile",
                    "arguments": {
                        "agent_id": "codex_worker",
                        "summary": "Parser and pytest specialist.",
                        "knows_about": ["parser failures"],
                        "can_do": ["run pytest"],
                        "specialty_tags": ["parser", "pytest"],
                    },
                },
            },
        )
        assert updated_profile["result"]["structuredContent"]["profile"]["agent_id"] == "codex_worker"

        profiles = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 33,
                "method": "tools/call",
                "params": {
                    "name": "get_agent_profiles",
                    "arguments": {"agent_id": "engineering_manager"},
                },
            },
        )
        profile_ids = {profile["agent_id"] for profile in profiles["result"]["structuredContent"]["profiles"]}
        assert "codex_worker" in profile_ids

        report = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "report",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "to_agent_id": "engineering_manager",
                        "task_id": task.task_id,
                        "summary": "Fixed the failing parser test.",
                        "status": "completed",
                        "work_done": ["Inspected failure", "Patched parser", "Ran tests"],
                        "evidence": [{"type": "test_log", "path": "artifacts/task_001/pytest.log"}],
                        "risks": [],
                        "blockers": [],
                        "confidence": 0.86,
                        "cost": {
                            "tokens_used": 12000,
                            "runtime_seconds": 240,
                            "tool_calls": 8,
                        },
                        "next_action": "Ready for manager review.",
                        "requires_decision": False,
                        "alignment_check": "Meets task acceptance criteria.",
                    },
                },
            },
        )
        assert report["result"]["structuredContent"]["ok"] is True
        report_id = report["result"]["structuredContent"]["report_id"]

        review = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 34,
                "method": "tools/call",
                "params": {
                    "name": "review_report",
                    "arguments": {
                        "from_agent_id": "engineering_manager",
                        "report_id": report_id,
                        "decision": "accept",
                        "notes": "Evidence is sufficient.",
                    },
                },
            },
        )
        assert review["result"]["structuredContent"]["ok"] is True
        assert review["result"]["structuredContent"]["decision"] == "accept"

        human_report = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "report_to_human",
                    "arguments": {
                        "from_agent_id": "ceo",
                        "task_id": task.task_id,
                        "title": "Final CEO report",
                        "message": "The parser task is complete and ready for human review.",
                        "status": "completed",
                        "confidence": 0.91,
                        "next_action": "Human can review the trace.",
                    },
                },
            },
        )
        assert human_report["result"]["structuredContent"]["ok"] is True
        assert human_report["result"]["structuredContent"]["human_report_id"].startswith("human_report_")

        rejected = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "report_to_human",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "task_id": task.task_id,
                        "message": "Worker should not be able to report directly to human.",
                    },
                },
            },
        )
        assert "error" in rejected
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    assert process.returncode == 0
    with WorkforceRuntime(db_path) as runtime:
        reports = runtime.store.list_reports_by_task(task.task_id)
        assert len(reports) == 1
        assert reports[0].report_id == report_id
        assert reports[0].summary == "Fixed the failing parser test."
        assert reports[0].cost.tool_calls == 8
        assert runtime.require_task(task.task_id).status == "completed"
        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "report_registered" in event_types
        assert "manager_review_decided" in event_types
        assert "human_report_registered" in event_types
        assert "mcp_tool_call_started" in event_types
        assert "mcp_tool_call_finished" in event_types
