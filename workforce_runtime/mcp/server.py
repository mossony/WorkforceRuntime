from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from workforce_runtime.config import load_runtime_config
from workforce_runtime.mcp.external import ExternalMCPRegistry, ResolvedExternalMCPTool
from workforce_runtime.mcp.tools.get_agent_profiles import get_agent_profiles
from workforce_runtime.mcp.tools.get_org_context import get_org_context
from workforce_runtime.mcp.tools.get_task_context import get_task_context
from workforce_runtime.mcp.tools.assign import assign
from workforce_runtime.mcp.tools.check_progress import check_progress
from workforce_runtime.mcp.tools.claim_inbox import claim_inbox
from workforce_runtime.mcp.tools.claim_work import claim_work
from workforce_runtime.mcp.tools.complete_inbox import complete_inbox
from workforce_runtime.mcp.tools.complete_work import complete_work
from workforce_runtime.mcp.tools.decide_tool_request import decide_tool_request
from workforce_runtime.mcp.tools.discuss import discuss
from workforce_runtime.mcp.tools.enqueue_work import enqueue_work
from workforce_runtime.mcp.tools.fail_inbox import fail_inbox
from workforce_runtime.mcp.tools.fail_work import fail_work
from workforce_runtime.mcp.tools.get_inbox import get_inbox
from workforce_runtime.mcp.tools.get_work_queue import get_work_queue
from workforce_runtime.mcp.tools.hire import hire
from workforce_runtime.mcp.tools.get_task_dossier import get_task_dossier
from workforce_runtime.mcp.tools.report import report
from workforce_runtime.mcp.tools.report_to_human import report_to_human
from workforce_runtime.mcp.tools.review_report import review_report
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
    "review_report": review_report,
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
    "get_inbox": get_inbox,
    "claim_inbox": claim_inbox,
    "complete_inbox": complete_inbox,
    "fail_inbox": fail_inbox,
    "enqueue_work": enqueue_work,
    "claim_work": claim_work,
    "complete_work": complete_work,
    "fail_work": fail_work,
    "get_work_queue": get_work_queue,
}

QUEUE_CONTROL_TOOLS = {
    "enqueue_work",
    "claim_work",
    "complete_work",
    "fail_work",
    "get_work_queue",
    "get_inbox",
    "claim_inbox",
    "complete_inbox",
    "fail_inbox",
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
            "name": "review_report",
            "description": "Manager reviews a subordinate report and records an explicit decision.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "report_id", "decision"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "reviewer_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "report_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["accept", "reject", "request_retry", "escalate", "request_human_review"],
                    },
                    "notes": {"type": "string"},
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
        {
            "name": "get_inbox",
            "description": "Return queued or leased inbox items for an agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["queued", "leased", "completed", "failed", "cancelled", "interrupted"],
                    },
                },
            },
        },
        {
            "name": "claim_inbox",
            "description": "Claim the next RabbitMQ-backed inbox items for an agent.",
            "inputSchema": {
                "type": "object",
                "required": ["agent_id"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "lease_owner": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "complete_inbox",
            "description": "Mark an inbox item completed after the agent handles it.",
            "inputSchema": {
                "type": "object",
                "required": ["inbox_item_id"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "inbox_item_id": {"type": "string"},
                    "result": {"type": "object"},
                },
            },
        },
        {
            "name": "fail_inbox",
            "description": "Fail or requeue an inbox item.",
            "inputSchema": {
                "type": "object",
                "required": ["inbox_item_id", "error"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "inbox_item_id": {"type": "string"},
                    "error": {"type": "string"},
                    "retry": {"type": "boolean"},
                },
            },
        },
        {
            "name": "enqueue_work",
            "description": "Queue an LLM request, tool call, or worker run for bounded scheduler execution.",
            "inputSchema": {
                "type": "object",
                "required": ["from_agent_id", "agent_id", "kind"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["llm_request", "tool_call", "worker_run"]},
                    "payload": {"type": "object"},
                    "priority": {"type": "integer"},
                    "model": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                    "max_attempts": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "claim_work",
            "description": "Claim queued work items under max-active-agent, model, tool, and kind limits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "lease_owner": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                    "policy": {"type": "object"},
                },
            },
        },
        {
            "name": "complete_work",
            "description": "Mark a leased work item as completed and store its result.",
            "inputSchema": {
                "type": "object",
                "required": ["work_item_id"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "work_item_id": {"type": "string"},
                    "result": {"type": "object"},
                },
            },
        },
        {
            "name": "fail_work",
            "description": "Mark a work item failed or requeue it when attempts remain.",
            "inputSchema": {
                "type": "object",
                "required": ["work_item_id", "error"],
                "properties": {
                    "from_agent_id": {"type": "string"},
                    "work_item_id": {"type": "string"},
                    "error": {"type": "string"},
                    "retry": {"type": "boolean"},
                },
            },
        },
        {
            "name": "get_work_queue",
            "description": "Return the current persistent work queue state.",
            "inputSchema": {"type": "object"},
        },
    ]


class MCPServer:
    def __init__(
        self,
        runtime: WorkforceRuntime,
        config: dict[str, Any] | None = None,
        *,
        default_actor_id: str = "",
    ) -> None:
        self.runtime = runtime
        self.config = config or load_runtime_config()
        self.default_actor_id = default_actor_id
        self.external_mcp = ExternalMCPRegistry(self.config)

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
                params = message.get("params") or {}
                actor_id = self.default_actor_id
                if isinstance(params, dict) and params.get("actor_id"):
                    actor_id = str(params["actor_id"])
                result = {"tools": [*tool_specs(), *self.external_mcp.tool_specs(self.runtime, actor_id=actor_id)]}
            elif method == "tools/call":
                params = message.get("params") or {}
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                name = str(params["name"])
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                resolved_external = self.external_mcp.resolve(name)
                if name not in TOOL_HANDLERS and resolved_external is None:
                    raise ValueError(f"unknown tool: {name}")
                actor_id = _tool_actor_id(arguments, default_actor_id=self.default_actor_id)
                task_id = _tool_task_id(arguments)
                self.runtime.record_event(
                    event_type="mcp_tool_call_started",
                    actor_id=actor_id,
                    task_id=task_id,
                    payload={"tool_name": name, **_summarize_tool_arguments(arguments)},
                )
                try:
                    structured = self._execute_tool(
                        name,
                        arguments,
                        actor_id=actor_id,
                        task_id=task_id,
                        resolved_external=resolved_external,
                    )
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

    def _execute_tool(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        actor_id: str,
        task_id: str | None,
        resolved_external: ResolvedExternalMCPTool | None = None,
    ) -> dict[str, object]:
        if resolved_external is not None:
            executor = lambda: self.external_mcp.execute(
                self.runtime,
                resolved_external,
                arguments,
                actor_id=actor_id,
                task_id=task_id,
            )
            if not _should_queue_external_mcp_tool(
                self.runtime,
                self.config,
                resolved_external,
                actor_id=actor_id,
            ):
                return executor()
            return self._execute_queued_tool(
                name,
                arguments,
                actor_id=actor_id,
                task_id=task_id,
                executor=executor,
            )
        if not _should_queue_mcp_tool(self.runtime, self.config, name=name, actor_id=actor_id):
            return TOOL_HANDLERS[name](self.runtime, arguments)
        return self._execute_queued_tool(name, arguments, actor_id=actor_id, task_id=task_id)

    def _execute_queued_tool(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        actor_id: str,
        task_id: str | None,
        executor: Callable[[], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        work_item = self.runtime.enqueue_work_item(
            actor_id=actor_id,
            agent_id=actor_id,
            kind="tool_call",
            task_id=task_id,
            payload={"tool_name": name, "arguments": arguments},
            tool_name=name,
            max_attempts=1,
        )
        self.runtime.record_event(
            event_type="mcp_tool_call_queued",
            actor_id=actor_id,
            task_id=task_id,
            payload={"tool_name": name, "work_item_id": work_item.work_item_id},
        )

        queue_policy = dict(self.config.get("queue") or {})
        queue_policy["allow_same_agent_parallel"] = True
        timeout_seconds = _mcp_tool_queue_timeout_seconds(self.config)
        deadline = time.monotonic() + timeout_seconds
        lease_owner = f"mcp_tool:{name}"
        while True:
            claimed = self.runtime.claim_work_item(work_item.work_item_id, lease_owner=lease_owner, policy=queue_policy)
            if claimed is not None:
                break
            if time.monotonic() >= deadline:
                self.runtime.fail_work_item(
                    work_item.work_item_id,
                    actor_id=lease_owner,
                    error=f"timed out waiting for MCP tool queue slot: {name}",
                    retry=False,
                )
                raise TimeoutError(f"timed out waiting for MCP tool queue slot: {name}")
            time.sleep(0.05)

        try:
            structured = executor() if executor is not None else TOOL_HANDLERS[name](self.runtime, arguments)
        except Exception as exc:
            self.runtime.fail_work_item(work_item.work_item_id, actor_id=lease_owner, error=str(exc), retry=False)
            raise
        self.runtime.complete_work_item(work_item.work_item_id, actor_id=lease_owner, result={"tool_result": structured})
        return structured


def _tool_actor_id(arguments: dict[str, object], *, default_actor_id: str = "") -> str:
    for key in ("from_agent_id", "agent_id", "caller_id", "requested_by"):
        if arguments.get(key):
            return str(arguments[key])
    if default_actor_id:
        return default_actor_id
    return "unknown"


def _tool_task_id(arguments: dict[str, object]) -> str | None:
    if arguments.get("task_id"):
        return str(arguments["task_id"])
    return None


def _should_queue_mcp_tool(
    runtime: WorkforceRuntime,
    config: dict[str, Any],
    *,
    name: str,
    actor_id: str,
) -> bool:
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    if str(execution.get("mode") or "full_access") != "sandbox":
        return False
    sandbox = execution.get("sandbox") if isinstance(execution.get("sandbox"), dict) else {}
    if not bool(sandbox.get("queue_mcp_tools", False)):
        return False
    excluded = sandbox.get("mcp_tool_queue_excluded_tools") or list(QUEUE_CONTROL_TOOLS)
    if name in {str(item) for item in excluded}:
        return False
    if name in QUEUE_CONTROL_TOOLS:
        return False
    if actor_id == "unknown" or runtime.store.get_agent(actor_id) is None:
        return False
    return True


def _should_queue_external_mcp_tool(
    runtime: WorkforceRuntime,
    config: dict[str, Any],
    resolved: ResolvedExternalMCPTool,
    *,
    actor_id: str,
) -> bool:
    if not resolved.server.queue_enabled:
        return False
    if actor_id == "unknown" or runtime.store.get_agent(actor_id) is None:
        return False
    external = config.get("external_mcp") if isinstance(config.get("external_mcp"), dict) else {}
    if not bool(external.get("queue_calls", True)):
        return False
    return True


def _mcp_tool_queue_timeout_seconds(config: dict[str, Any]) -> float:
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    sandbox = execution.get("sandbox") if isinstance(execution.get("sandbox"), dict) else {}
    value = sandbox.get("mcp_tool_queue_timeout_seconds", 30.0)
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return 30.0


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
        "agent_id",
        "inbox_item_id",
        "status",
        "work_item_id",
        "kind",
        "lease_owner",
        "model",
        "tool_name",
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
        "work_item_id",
        "inbox_item_id",
        "claimed_count",
        "count",
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


def serve_stdio(db_path: str | Path, config: dict[str, Any] | None = None) -> None:
    with WorkforceRuntime(db_path) as runtime:
        server = MCPServer(runtime, config=config, default_actor_id=os.environ.get("WORKFORCE_AGENT_ID", ""))
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
    env_db = os.environ.get("WORKFORCE_RUNTIME_DB")
    db_path = args.db or (Path(env_db) if env_db else Path(str(config.get("runtime", {}).get("db_path") or "workforce_runtime")))
    serve_stdio(db_path, config=config)


if __name__ == "__main__":
    main()
