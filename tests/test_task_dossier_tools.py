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


def test_task_dossier_docs_and_tool_request_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Research parser automation",
            objective="Find repeated parser validation steps and propose a tool if needed.",
            assign_to="codex_worker",
            constraints=["Preserve evidence"],
            acceptance_criteria=["Document the need", "Request tool if repeated work is found"],
            required_artifacts=["research_note"],
        )
        child = runtime.create_task(
            title="Peer review automation idea",
            objective="Review the proposed tool request.",
            assign_to="claude_worker",
            assigned_by="engineering_manager",
            parent_task_id=task.task_id,
        )

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
        assert {"get_task_dossier", "upsert_task_doc", "request_tool", "decide_tool_request"} <= tool_names

        doc = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "upsert_task_doc",
                    "arguments": {
                        "agent_id": "codex_worker",
                        "task_id": task.task_id,
                        "doc_type": "requirements",
                        "title": "Parser automation requirements",
                        "content": "Repeated validation should be batched and evidence should be retained.",
                    },
                },
            },
        )
        doc_id = doc["result"]["structuredContent"]["document"]["doc_id"]

        dossier = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_task_dossier",
                    "arguments": {"agent_id": "codex_worker", "task_id": task.task_id},
                },
            },
        )
        content = dossier["result"]["structuredContent"]
        assert content["requirements"]["required_artifacts"] == ["research_note"]
        assert content["documents"][0]["doc_id"] == doc_id
        assert content["division_of_work"][0]["task_id"] == child.task_id

        requested = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "request_tool",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "task_id": task.task_id,
                        "tool_name": "batch_parser_validator",
                        "problem": "Workers repeatedly validate the same parser cases by hand.",
                        "proposed_capability": "Run a reusable parser case matrix and attach a summarized artifact.",
                        "frequency": "Observed in multiple parser tasks.",
                        "current_workaround": "Manual script snippets in each worker run.",
                        "requested_approval_level": "vp",
                    },
                },
            },
        )
        request_id = requested["result"]["structuredContent"]["request_id"]

        approved = send_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "decide_tool_request",
                    "arguments": {
                        "from_agent_id": "vp_engineering",
                        "request_id": request_id,
                        "decision": "approved",
                        "approval_level": "vp",
                        "notes": "Repeated work justifies a reusable tool.",
                    },
                },
            },
        )
        assert approved["result"]["structuredContent"]["decision"] == "approved"
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    with WorkforceRuntime(db_path) as runtime:
        event_types = [event.event_type for event in runtime.store.list_events()]
        docs = runtime.store.list_task_documents_by_task(task.task_id)

    assert "task_document_upserted" in event_types
    assert "tool_request_submitted" in event_types
    assert "tool_request_approved" in event_types
    assert any(doc.doc_type == "tool_request" for doc in docs)
