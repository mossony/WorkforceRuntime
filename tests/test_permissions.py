from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from workforce_runtime.core import Artifact
from workforce_runtime.dashboard import render_text_dashboard
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_agent_without_delegate_task_cannot_delegate(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        with pytest.raises(PermissionError):
            runtime.create_task(
                title="Unauthorized delegation",
                objective="Codex worker should not delegate.",
                assign_to="claude_worker",
                assigned_by="codex_worker",
            )

        violation = next(event for event in runtime.store.list_events() if event.event_type == "permission_violation")
        assert violation.actor_id == "codex_worker"
        assert violation.payload["capability"] == "delegate_task"
        assert "codex_worker triggered permission_violation" in render_text_dashboard(runtime.store)


def test_agent_without_submit_artifact_cannot_submit_artifact(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Manager artifact",
            objective="Manager lacks artifact permission.",
            assign_to="engineering_manager",
        )

        with pytest.raises(PermissionError):
            runtime.register_artifact(
                Artifact(
                    artifact_id="artifact_001",
                    task_id=task.task_id,
                    agent_id="engineering_manager",
                    type="note",
                    path="artifacts/task_001/note.md",
                )
            )

        violation = next(event for event in runtime.store.list_events() if event.event_type == "permission_violation")
        assert violation.payload["capability"] == "submit_artifact"


def test_mcp_request_budget_enforces_permission(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        runtime.create_task(
            title="Budget request",
            objective="Codex worker lacks request_budget.",
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
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "request_budget",
                        "arguments": {
                            "agent_id": "codex_worker",
                            "task_id": "task_001",
                            "tokens": 100,
                        },
                    },
                }
            )
            + "\n"
        )
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    assert "error" in response
    assert "request_budget" in response["error"]["message"]
    with WorkforceRuntime(db_path) as runtime:
        violation = next(event for event in runtime.store.list_events() if event.event_type == "permission_violation")
        assert violation.payload["capability"] == "request_budget"
