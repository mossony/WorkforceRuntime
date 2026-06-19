from __future__ import annotations

import json
import os
import subprocess
import sys


def mcp_request(process: subprocess.Popen[str], message: dict[str, object]) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def main() -> None:
    task_path = os.environ["WORKFORCE_TASK_CONTRACT_PATH"]
    db_path = os.environ["WORKFORCE_RUNTIME_DB"]
    agent_id = os.environ["WORKFORCE_AGENT_ID"]
    manager_id = os.environ.get("WORKFORCE_MANAGER_ID") or "manager"

    with open(task_path) as task_file:
        task = json.load(task_file)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            db_path,
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
        mcp_request(
            process,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        response = mcp_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "report",
                    "arguments": {
                        "from_agent_id": agent_id,
                        "to_agent_id": manager_id,
                        "task_id": task["task_id"],
                        "summary": f"Mock worker completed: {task['title']}",
                        "status": "completed",
                        "work_done": ["Read task contract", "Submitted report through MCP"],
                        "evidence": [],
                        "risks": [],
                        "blockers": [],
                        "confidence": 0.75,
                        "cost": {
                            "tokens_used": 0,
                            "runtime_seconds": 0,
                            "tool_calls": 1,
                        },
                        "next_action": "Ready for manager review.",
                        "requires_decision": False,
                        "alignment_check": "Mock worker followed the task contract.",
                    },
                },
            },
        )
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    if "error" in response:
        print(response["error"], file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps(response["result"]["structuredContent"]))


if __name__ == "__main__":
    main()
