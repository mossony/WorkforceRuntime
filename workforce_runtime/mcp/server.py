from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from workforce_runtime.mcp.tools.get_org_context import get_org_context
from workforce_runtime.mcp.tools.get_task_context import get_task_context
from workforce_runtime.mcp.tools.assign import assign
from workforce_runtime.mcp.tools.check_progress import check_progress
from workforce_runtime.mcp.tools.discuss import discuss
from workforce_runtime.mcp.tools.hire import hire
from workforce_runtime.mcp.tools.report import report
from workforce_runtime.mcp.tools.request_budget import request_budget
from workforce_runtime.mcp.tools.request_permission import request_permission
from workforce_runtime.mcp.tools.submit_artifact import submit_artifact
from workforce_runtime.mcp.tools.update_system_prompt import update_system_prompt
from workforce_runtime.mcp.tools.update_status import update_status
from workforce_runtime.server.runtime import WorkforceRuntime

ToolHandler = Callable[[WorkforceRuntime, dict[str, object]], dict[str, object]]


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "report": report,
    "assign": assign,
    "check_progress": check_progress,
    "discuss": discuss,
    "hire": hire,
    "update_system_prompt": update_system_prompt,
    "submit_artifact": submit_artifact,
    "update_status": update_status,
    "request_budget": request_budget,
    "request_permission": request_permission,
    "get_task_context": get_task_context,
    "get_org_context": get_org_context,
}


def tool_specs() -> list[dict[str, object]]:
    return [
        {
            "name": "report",
            "description": "Submit a structured task report to the caller's direct manager.",
            "inputSchema": {
                "type": "object",
                "required": [
                    "from_agent_id",
                    "task_id",
                    "summary",
                    "status",
                    "confidence",
                ],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "to_agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "status": {"type": "string"},
                    "work_done": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "blockers": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "cost": {"type": "object"},
                    "next_action": {"type": "string"},
                    "requires_decision": {"type": "boolean"},
                    "alignment_check": {"type": "string"},
                },
            },
        },
        {
            "name": "assign",
            "description": "Create or assign a task to an agent managed by the caller.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "to_agent_id", "message"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "to_agent_id": {"type": "string"},
                    "id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "required_artifacts": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        {
            "name": "discuss",
            "description": "Send a message to another worker or manager.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "to_agent_id", "message"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "to_agent_id": {"type": "string"},
                    "id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "thread_id": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
        {
            "name": "check_progress",
            "description": "Manager asks for the current status of a subordinate and records the check.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "target_agent_id"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "target_agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
        {
            "name": "hire",
            "description": "Hire a new agent when HR permission and company budget allow it.",
            "inputSchema": {"type": "object"},
        },
        {
            "name": "update_system_prompt",
            "description": "Update the system prompt of an agent under the caller's reporting line.",
            "inputSchema": {"type": "object"},
        },
        {"name": "submit_artifact", "description": "Register an artifact.", "inputSchema": {"type": "object"}},
        {"name": "update_status", "description": "Update task status.", "inputSchema": {"type": "object"}},
        {"name": "request_budget", "description": "Request more budget.", "inputSchema": {"type": "object"}},
        {
            "name": "request_permission",
            "description": "Request an additional permission.",
            "inputSchema": {"type": "object"},
        },
        {"name": "get_task_context", "description": "Get task context.", "inputSchema": {"type": "object"}},
        {"name": "get_org_context", "description": "Get organization context.", "inputSchema": {"type": "object"}},
    ]


class MCPServer:
    def __init__(self, runtime: WorkforceRuntime) -> None:
        self.runtime = runtime

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")

        if request_id is None:
            return None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "workforce-runtime", "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {"tools": tool_specs()}
            elif method == "tools/call":
                params = message.get("params") or {}
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                name = str(params["name"])
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                if name not in TOOL_HANDLERS:
                    raise ValueError(f"unknown tool: {name}")
                actor_id = _tool_actor_id(arguments)
                task_id = _tool_task_id(arguments)
                self.runtime.record_event(
                    event_type="mcp_tool_call_started",
                    actor_id=actor_id,
                    task_id=task_id,
                    payload={"tool_name": name, **_summarize_tool_arguments(arguments)},
                )
                try:
                    structured = TOOL_HANDLERS[name](self.runtime, arguments)
                except Exception as exc:
                    self.runtime.record_event(
                        event_type="mcp_tool_call_failed",
                        actor_id=actor_id,
                        task_id=task_id,
                        payload={"tool_name": name, "error": _clip(str(exc))},
                    )
                    raise
                self.runtime.record_event(
                    event_type="mcp_tool_call_finished",
                    actor_id=actor_id,
                    task_id=task_id,
                    payload={"tool_name": name, **_summarize_tool_result(structured)},
                )
                result = {
                    "content": [{"type": "text", "text": json.dumps(structured)}],
                    "structuredContent": structured,
                    "isError": False,
                }
            else:
                raise ValueError(f"unsupported method: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }


def _tool_actor_id(arguments: dict[str, object]) -> str:
    for key in ("from_agent_id", "agent_id", "caller_id", "requested_by"):
        if arguments.get(key):
            return str(arguments[key])
    return "unknown"


def _tool_task_id(arguments: dict[str, object]) -> str | None:
    if arguments.get("task_id"):
        return str(arguments["task_id"])
    return None


def _summarize_tool_arguments(arguments: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key in ("to_agent_id", "target_agent_id", "assignee_id", "worker_id", "recipient_id", "thread_id"):
        if arguments.get(key):
            summary[key] = str(arguments[key])
    if arguments.get("message"):
        summary["message"] = _clip(str(arguments["message"]))
    if arguments.get("title"):
        summary["title"] = _clip(str(arguments["title"]))
    return summary


def _summarize_tool_result(result: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {"ok": bool(result.get("ok", True))}
    for key in ("task_id", "report_id", "event_id", "assigned_to", "to_agent_id", "status"):
        if result.get(key) is not None:
            summary[key] = str(result[key])
    return summary


def _clip(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def serve_stdio(db_path: str | Path) -> None:
    with WorkforceRuntime(db_path) as runtime:
        server = MCPServer(runtime)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = server.handle(json.loads(line))
            if response is None:
                continue
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workforce-runtime-mcp")
    parser.add_argument("--db", type=Path, default=Path(".workforce_runtime/runtime.sqlite"))
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    serve_stdio(args.db)


if __name__ == "__main__":
    main()
