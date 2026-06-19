from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import urllib.request
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
    manager_id = os.environ.get("WORKFORCE_MANAGER_ID") or "engineering_manager"

    task = json.loads(task_path.read_text())
    url = os.environ.get("WORKFORCE_WEB_RESEARCH_URL", "https://www.iana.org/help/example-domains")
    artifact_path = workspace / "artifacts" / task["task_id"] / "web_research_summary.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"worker={agent_id} loading task {task['task_id']}", flush=True)
    print(f"fetching {url}", flush=True)

    status = "completed"
    blockers: list[str] = []
    title = ""
    final_url = url
    byte_count = 0
    fact_lines: list[str] = []

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "WorkforceRuntimeDemo/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            final_url = response.geturl()
            body = response.read()
        text = body.decode("utf-8", errors="replace")
        byte_count = len(body)
        title_match = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        title = html.unescape(" ".join(title_match.group(1).split())) if title_match else "untitled page"
        for domain in ("example.com", "example.net", "example.org"):
            if domain in text:
                fact_lines.append(f"- Observed `{domain}` on the fetched IANA page.")
        print(f"fetched {byte_count} bytes; title={title}", flush=True)
    except Exception as exc:  # noqa: BLE001 - demo worker should report network failures.
        status = "failed"
        blockers.append(f"network fetch failed: {exc}")
        print(blockers[-1], flush=True)

    artifact_path.write_text(
        "\n".join(
            [
                "# Web Research Summary",
                "",
                f"Task: `{task['task_id']}`",
                f"URL: {url}",
                f"Final URL: {final_url}",
                f"Page title: {title or 'unavailable'}",
                f"Downloaded bytes: {byte_count}",
                "",
                "Findings:",
                *(fact_lines or ["- No domain markers were extracted."]),
                "",
                f"Status: {status}",
            ]
        )
        + "\n"
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
            "get_task_context",
            {"agent_id": agent_id, "task_id": task["task_id"]},
        )
        call_tool(
            process,
            3,
            "discuss",
            {
                "from_agent_id": agent_id,
                "to_agent_id": "claude_worker",
                "task_id": task["task_id"],
                "message": "Fetched the source page and produced a short research artifact for review.",
            },
        )
        call_tool(
            process,
            4,
            "submit_artifact",
            {
                "agent_id": agent_id,
                "task_id": task["task_id"],
                "artifact_type": "web_research_summary",
                "path": str(artifact_path),
                "description": "Summary of the fetched public web page.",
            },
        )
        response = call_tool(
            process,
            5,
            "report",
            {
                "from_agent_id": agent_id,
                "to_agent_id": manager_id,
                "task_id": task["task_id"],
                "summary": f"Fetched {url} and wrote a web research summary.",
                "status": status,
                "work_done": [
                    "Read the task context through MCP",
                    "Fetched a public internet page",
                    "Submitted a web research artifact",
                    "Reported completion to the direct manager",
                ],
                "evidence": [{"type": "web_research_summary", "path": str(artifact_path)}],
                "risks": [],
                "blockers": blockers,
                "confidence": 0.9 if status == "completed" else 0.2,
                "cost": {"tokens_used": 0, "runtime_seconds": 0, "tool_calls": 4},
                "next_action": "Manager can review the research artifact.",
                "requires_decision": False,
                "alignment_check": "Artifact includes source URL, fetch metadata, findings, and status.",
            },
        )
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)

    print(json.dumps(response["result"]["structuredContent"]), flush=True)
    raise SystemExit(0 if status == "completed" else 1)


if __name__ == "__main__":
    main()
