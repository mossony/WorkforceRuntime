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
        runtime.create_task(
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
        assert "report" in {tool["name"] for tool in tools["result"]["tools"]}

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
                        "task_id": "task_001",
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
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    assert process.returncode == 0
    with WorkforceRuntime(db_path) as runtime:
        reports = runtime.store.list_reports_by_task("task_001")
        assert len(reports) == 1
        assert reports[0].report_id == report_id
        assert reports[0].summary == "Fixed the failing parser test."
        assert reports[0].cost.tool_calls == 8
        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "report_registered" in event_types
        assert "mcp_tool_call_started" in event_types
        assert "mcp_tool_call_finished" in event_types
