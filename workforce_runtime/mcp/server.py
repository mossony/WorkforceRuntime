from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from workforce_runtime.config import load_runtime_config
from workforce_runtime.mcp.tools.get_agent_profiles import get_agent_profiles
from workforce_runtime.mcp.tools.get_org_context import get_org_context
from workforce_runtime.mcp.tools.get_task_context import get_task_context
from workforce_runtime.mcp.tools.assign import assign
from workforce_runtime.mcp.tools.check_progress import check_progress
from workforce_runtime.mcp.tools.decide_tool_request import decide_tool_request
from workforce_runtime.mcp.tools.discuss import discuss
from workforce_runtime.mcp.tools.hire import hire
from workforce_runtime.mcp.tools.get_task_dossier import get_task_dossier
from workforce_runtime.mcp.tools.report import report
from workforce_runtime.mcp.tools.report_to_human import report_to_human
from workforce_runtime.mcp.tools.request_budget import request_budget
from workforce_runtime.mcp.tools.request_permission import request_permission
from workforce_runtime.mcp.tools.request_tool import request_tool
from workforce_runtime.mcp.tools.submit_artifact import submit_artifact
from workforce_runtime.mcp.tools.update_agent_profile import update_agent_profile
from workforce_runtime.mcp.tools.update_system_prompt import update_system_prompt
from workforce_runtime.mcp.tools.update_status import update_status
from workforce_runtime.mcp.tools.upsert_task_doc import upsert_task_doc
from workforce_runtime.server.runtime import WorkforceRuntime

ToolHandler = Callable[[WorkforceRuntime, dict[str, object]], dict[str, object]]


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "report": report,
    "report_to_human": report_to_human,
    "assign": assign,
    "check_progress": check_progress,
    "discuss": discuss,
    "hire": hire,
    "update_system_prompt": update_system_prompt,
    "update_agent_profile": update_agent_profile,
    "get_agent_profiles": get_agent_profiles,
    "get_task_dossier": get_task_dossier,
    "upsert_task_doc": upsert_task_doc,
    "request_tool": request_tool,
    "decide_tool_request": decide_tool_request,
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
            "name": "report_to_human",
            "description": "CEO-only final report or decision update for the human operator.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "message"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "status": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "next_action": {"type": "string"},
                    "requires_decision": {"type": "boolean"},
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
        {
            "name": "update_agent_profile",
            "description": "Update the caller's reusable personal profile: specialties, capabilities, boundaries, preferred tools, and task experience.",
            "inputSchema": {
                "type": "object",
                "required": ["agent_id"],
                "properties": {
                    "agent_id": {"type": "string"},
                    "from_agent_id": {"type": "string"},
                    "target_agent_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "knows_about": {"type": "array", "items": {"type": "string"}},
                    "can_do": {"type": "array", "items": {"type": "string"}},
                    "specialty_tags": {"type": "array", "items": {"type": "string"}},
                    "preferred_tools": {"type": "array", "items": {"type": "string"}},
                    "boundaries": {"type": "array", "items": {"type": "string"}},
                    "task_id": {"type": "string"},
                    "task_title": {"type": "string"},
                    "task_summary": {"type": "string"},
                    "outcome": {"type": "string"},
                    "experience": {"type": "object"},
                },
            },
        },
        {
            "name": "get_agent_profiles",
            "description": "Read reusable personal profiles for the caller and agents under the caller's reporting line.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "actor_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "from_agent_id": {"type": "string"},
                    "target_agent_id": {"type": "string"},
                },
            },
        },
        {
            "name": "get_task_dossier",
            "description": "Read the task dossier: task contract, requirements, division of work, documents, reports, artifacts, and recent events.",
            "inputSchema": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "actor_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "include_events": {"type": "boolean"},
                    "event_limit": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "upsert_task_doc",
            "description": "Create or update a document attached to a task dossier.",
            "inputSchema": {
                "type": "object",
                "required": ["task_id", "title", "content"],
                "properties": {
                    "actor_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "doc_id": {"type": "string"},
                    "title": {"type": "string"},
                    "doc_type": {
                        "type": "string",
                        "enum": ["requirements", "division_of_work", "context", "decision", "note", "risk", "tool_request"],
                    },
                    "content": {"type": "string"},
                },
            },
        },
        {
            "name": "request_tool",
            "description": "Request a new runtime tool when repeated work lacks a suitable tool.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "tool_name", "problem", "proposed_capability"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "problem": {"type": "string"},
                    "proposed_capability": {"type": "string"},
                    "frequency": {"type": "string"},
                    "current_workaround": {"type": "string"},
                    "requested_approval_level": {"type": "string", "enum": ["human_ceo", "vp", "manager"]},
                },
            },
        },
        {
            "name": "decide_tool_request",
            "description": "Approve or reject a tool request according to the configured approval level.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "request_id", "decision"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["approved", "rejected"]},
                    "notes": {"type": "string"},
                    "approval_level": {"type": "string", "enum": ["human_ceo", "vp", "manager"]},
                },
            },
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
    for key in (
        "to_agent_id",
        "target_agent_id",
        "assignee_id",
        "worker_id",
        "recipient_id",
        "thread_id",
        "request_id",
        "doc_id",
        "doc_type",
        "profile_agent_id",
    ):
        if arguments.get(key):
            summary[key] = str(arguments[key])
    if arguments.get("tool_name"):
        summary["requested_tool_name"] = str(arguments["tool_name"])
    if arguments.get("message"):
        summary["message"] = _clip(str(arguments["message"]))
    if arguments.get("title"):
        summary["title"] = _clip(str(arguments["title"]))
    if arguments.get("problem"):
        summary["problem"] = _clip(str(arguments["problem"]))
    return summary


def _summarize_tool_result(result: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {"ok": bool(result.get("ok", True))}
    for key in (
        "task_id",
        "report_id",
        "event_id",
        "assigned_to",
        "to_agent_id",
        "status",
        "request_id",
        "decision",
        "approval_level",
        "human_report_id",
        "profile_agent_id",
        "revision",
    ):
        if result.get(key) is not None:
            summary[key] = str(result[key])
    profile = result.get("profile")
    if isinstance(profile, dict):
        for key in ("agent_id", "revision"):
            if profile.get(key) is not None:
                summary["profile_agent_id" if key == "agent_id" else key] = str(profile[key])
    document = result.get("document")
    if isinstance(document, dict):
        for key in ("doc_id", "doc_type", "title", "version"):
            if document.get(key) is not None:
                summary[key] = str(document[key])
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
    parser.add_argument("--config", type=Path, default=None, help="Path to the unified Workforce Runtime JSON config")
    parser.add_argument("--db", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = load_runtime_config(args.config)
    db_path = args.db or Path(str(config.get("runtime", {}).get("db_path") or ".workforce_runtime/runtime.sqlite"))
    serve_stdio(db_path)


if __name__ == "__main__":
    main()
