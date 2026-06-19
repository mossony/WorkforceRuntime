from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def mcp_request(process: subprocess.Popen[str], message: dict[str, object]) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def call_tool(
    process: subprocess.Popen[str],
    request_id: int,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    response = mcp_request(
        process,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    if "error" in response:
        raise RuntimeError(response["error"])
    return response


def main() -> None:
    task_path = Path(os.environ["WORKFORCE_TASK_CONTRACT_PATH"])
    db_path = os.environ["WORKFORCE_RUNTIME_DB"]
    workspace = Path(os.environ["WORKFORCE_WORKSPACE"])
    agent_id = os.environ["WORKFORCE_AGENT_ID"]

    task = json.loads(task_path.read_text())
    artifact_path = workspace / "artifacts" / task["task_id"] / "launch_note.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "# Launch Note\n\n"
        "Tiny Status Workforce completed the requested concise launch note.\n"
        "The worker used the assigned terminal model route and submitted this artifact.\n"
    )

    process = subprocess.Popen(
        [sys.executable, "-m", "workforce_runtime", "--db", db_path, "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        mcp_request(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        call_tool(
            process,
            2,
            "submit_artifact",
            {
                "agent_id": agent_id,
                "task_id": task["task_id"],
                "artifact_type": "launch_note",
                "path": str(artifact_path),
                "description": "Concise launch note produced by the terminal worker.",
            },
        )
        response = call_tool(
            process,
            3,
            "report",
            {
                "from_agent_id": agent_id,
                "task_id": task["task_id"],
                "summary": "Created concise launch note artifact.",
                "status": "completed",
                "work_done": ["Read assignment", "Created launch note", "Submitted artifact"],
                "evidence": [{"type": "launch_note", "path": str(artifact_path)}],
                "risks": [],
                "blockers": [],
                "confidence": 0.9,
                "cost": {"tokens_used": 0, "runtime_seconds": 0, "tool_calls": 2},
                "next_action": "Manager can review the launch note.",
                "requires_decision": False,
                "alignment_check": "Artifact is concise and matches the requested task.",
            },
        )
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    print(json.dumps(response["result"]["structuredContent"]))


if __name__ == "__main__":
    main()
