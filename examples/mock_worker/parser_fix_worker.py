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


def call_tool(process: subprocess.Popen[str], request_id: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return mcp_request(
        process,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


def main() -> None:
    db_path = os.environ["WORKFORCE_RUNTIME_DB"]
    task_id = os.environ["WORKFORCE_TASK_ID"]
    agent_id = os.environ["WORKFORCE_AGENT_ID"]
    manager_id = os.environ.get("WORKFORCE_MANAGER_ID") or "engineering_manager"
    workspace = Path(os.environ["WORKFORCE_WORKSPACE"])
    task_artifact_dir = workspace / "artifacts" / task_id
    task_artifact_dir.mkdir(parents=True, exist_ok=True)

    parser_path = workspace / "parser.py"
    parser_path.write_text(
        """from __future__ import annotations

import csv
from io import StringIO


def parse_csv_line(line: str) -> list[str]:
    return next(csv.reader(StringIO(line)))
"""
    )

    pytest_result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    test_log = task_artifact_dir / "pytest.log"
    test_log.write_text(pytest_result.stdout + pytest_result.stderr)

    diff_result = subprocess.run(
        ["git", "diff", "--", "parser.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    diff_path = task_artifact_dir / "diff.patch"
    diff_path.write_text(diff_result.stdout)

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
        mcp_request(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        call_tool(
            process,
            2,
            "submit_artifact",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "artifact_type": "git_diff",
                "path": str(diff_path),
                "description": "Parser fix diff.",
            },
        )
        call_tool(
            process,
            3,
            "submit_artifact",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "artifact_type": "test_log",
                "path": str(test_log),
                "description": "Pytest log for parser fix.",
            },
        )
        response = call_tool(
            process,
            4,
            "report",
            {
                "from_agent_id": agent_id,
                "to_agent_id": manager_id,
                "task_id": task_id,
                "summary": "Fixed quoted CSV parsing by using Python's csv module. Tests pass.",
                "status": "completed" if pytest_result.returncode == 0 else "failed",
                "work_done": [
                    "Replaced naive comma splitting with csv.reader",
                    "Ran pytest",
                    "Submitted diff and test log artifacts",
                ],
                "evidence": [
                    {"type": "git_diff", "path": str(diff_path)},
                    {"type": "test_log", "path": str(test_log)},
                ],
                "risks": [],
                "blockers": [],
                "confidence": 0.92 if pytest_result.returncode == 0 else 0.3,
                "cost": {
                    "tokens_used": 0,
                    "runtime_seconds": 1,
                    "tool_calls": 3,
                },
                "next_action": "Ready for manager review.",
                "requires_decision": False,
                "alignment_check": "Matches parser test acceptance criteria.",
            },
        )
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    if pytest_result.returncode != 0:
        print(test_log.read_text(), file=sys.stderr)
        raise SystemExit(pytest_result.returncode)
    if "error" in response:
        print(response["error"], file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps({"ok": True, "diff_path": str(diff_path), "test_log_path": str(test_log)}))


if __name__ == "__main__":
    main()
