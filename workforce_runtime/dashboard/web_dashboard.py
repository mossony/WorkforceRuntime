from __future__ import annotations

import argparse
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workforce_runtime.config import model_capabilities
from workforce_runtime.core.organization import Company
from workforce_runtime.dashboard.config import load_dashboard_config, merge_dashboard_config
from workforce_runtime.dashboard.summaries import total_budget_usage, worker_performance
from workforce_runtime.dashboard.text_dashboard import render_agent_trajectories
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage.sqlite_store import SequencedEvent
from workforce_runtime.storage import SQLiteStore


CODEX_ICON_PATH = Path("/Applications/Codex.app/Contents/Resources/icon-codex-light.png")


def build_web_dashboard_state(store: SQLiteStore, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = merge_dashboard_config(config)
    company = store.get_company() or Company(name="Unknown Workforce")
    agents = store.list_agents()
    tasks = store.list_tasks()
    reports = store.list_reports()
    artifacts = store.list_artifacts()
    events = store.list_events()
    budget = total_budget_usage(tasks, reports)

    recent_events = events[-_config_int(config, "activity", "recent_event_limit", default=300):]
    output = _agent_output(events)
    runs = _agent_runs(events)
    activity = _agent_activity(agents, events, config)
    return {
        "cursor": store.latest_event_sequence(),
        "config": config,
        "company": company.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "org_chart": _org_chart(agents, activity, config),
        "agent_activity": activity,
        "agent_summaries": {agent_id: data.get("summary", {}) for agent_id, data in activity.items()},
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "reports": [report.model_dump(mode="json") for report in reports],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "budget": {
            **budget,
            "token_budget_limit": company.token_budget or budget["max_tokens"],
            "headcount_limit": company.headcount_limit or None,
        },
        "agent_runs": runs,
        "worker_runs": runs,
        "agent_output": output[-_config_int(config, "activity", "global_output_limit", default=200):],
        "worker_output": output[-_config_int(config, "activity", "worker_output_limit", default=80):],
        "events": [_event_summary(event) for event in recent_events],
        "event_replay": _compact_event_replay(recent_events),
        "trajectories": render_agent_trajectories(store),
        "worker_performance": worker_performance(agents, tasks, reports, artifacts),
    }


def serve_web_dashboard(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    config_path: str | Path | None = None,
) -> None:
    server = make_web_dashboard_server(db_path, host=host, port=port, config_path=config_path)
    address = server.server_address
    print(f"Workforce Runtime web dashboard: http://{address[0]}:{address[1]}", flush=True)
    server.serve_forever()


def make_web_dashboard_server(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    db = Path(db_path)
    dashboard_config = load_dashboard_config(config_path) if config_path is not None else merge_dashboard_config(config)

    class DashboardHandler(BaseHTTPRequestHandler):
        def handle(self) -> None:
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(HTML)
                return
            if parsed.path == "/assets/agent-icons/codex.png":
                self._send_file(CODEX_ICON_PATH, content_type="image/png")
                return
            if parsed.path == "/api/state":
                with WorkforceRuntime(db) as runtime:
                    state = build_web_dashboard_state(runtime.store, dashboard_config)
                self._send_json(state)
                return
            if parsed.path == "/api/config":
                self._send_json(dashboard_config)
                return
            if parsed.path == "/api/events":
                query = parse_qs(parsed.query)
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["500"])[0])
                with WorkforceRuntime(db) as runtime:
                    items = runtime.store.list_events_after(after, limit=limit)
                    latest = runtime.store.latest_event_sequence()
                cursor = items[-1].sequence if items else latest
                self._send_json({"cursor": cursor, "events": [_sequenced_event_summary(item) for item in items]})
                return
            if parsed.path == "/api/events/stream":
                query = parse_qs(parsed.query)
                after = int(query.get("after", ["0"])[0])
                self._send_event_stream(db, after=after)
                return
            if parsed.path == "/api/replay":
                with WorkforceRuntime(db) as runtime:
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["500"])[0])
                    events = runtime.store.list_events()
                    self._send_text(_compact_event_replay(events[-limit:]))
                return
            if parsed.path == "/api/trajectories":
                with WorkforceRuntime(db) as runtime:
                    self._send_text(render_agent_trajectories(runtime.store))
                return
            if parsed.path == "/healthz":
                self._send_json({"ok": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_event_stream(self, db_path: Path, *, after: int) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            cursor = after
            last_heartbeat = time.monotonic()
            while True:
                try:
                    with WorkforceRuntime(db_path) as runtime:
                        items = runtime.store.list_events_after(cursor, limit=100)
                    for item in items:
                        cursor = item.sequence
                        self._write_sse("runtime_event", _sequenced_event_summary(item))
                    if time.monotonic() - last_heartbeat >= 10:
                        self._write_sse("heartbeat", {"cursor": cursor})
                        last_heartbeat = time.monotonic()
                    time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _write_sse(self, event_name: str, payload: dict[str, Any]) -> None:
            body = f"event: {event_name}\ndata: {json.dumps(payload)}\n\n".encode()
            self.wfile.write(body)
            self.wfile.flush()

        def _send_html(self, html: str) -> None:
            body = html.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str) -> None:
            body = text.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, *, content_type: str) -> None:
            if not path.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), DashboardHandler)


def add_web_dashboard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=None, help="Path to dashboard JSON config")


def _agent_runs(events: list[Any]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type not in {
            "worker_run_started",
            "worker_run_finished",
            "agent_run_started",
            "agent_run_finished",
        }:
            continue
        run_id = str(event.payload.get("run_id") or "")
        if not run_id:
            continue
        kind = "worker" if event.event_type.startswith("worker_") else "agent"
        run = runs.setdefault(
            run_id,
            {
                "run_id": run_id,
                "task_id": event.task_id,
                "agent_id": event.actor_id,
                "kind": kind,
                "status": "running",
                "executable": event.payload.get("executable") or "",
                "adapter": event.payload.get("adapter") or "",
                "model": event.payload.get("model") or "",
                "returncode": None,
                "timed_out": False,
                "error": "",
            },
        )
        run["task_id"] = event.task_id or run["task_id"]
        run["agent_id"] = event.actor_id
        run["kind"] = kind
        if event.event_type in {"worker_run_started", "agent_run_started"}:
            run["status"] = "running"
            run["executable"] = event.payload.get("executable") or run["executable"]
            run["adapter"] = event.payload.get("adapter") or run["adapter"]
            run["model"] = event.payload.get("model") or run["model"]
        elif event.event_type == "worker_run_finished":
            run["returncode"] = event.payload.get("returncode")
            run["timed_out"] = bool(event.payload.get("timed_out"))
            run["status"] = "timed_out" if run["timed_out"] else "finished"
        else:
            run["status"] = str(event.payload.get("status") or "finished")
            run["error"] = str(event.payload.get("error") or "")
    return list(runs.values())


def _agent_output(events: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in events:
        if event.event_type not in {"worker_output", "agent_output"}:
            continue
        output.append(_agent_output_item(event))
    return output


def _agent_activity(agents: list[Any], events: list[Any], config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output_limit = _config_int(config, "activity", "recent_output_items", default=12)
    tool_limit = _config_int(config, "activity", "recent_tool_items", default=12)
    event_limit = _config_int(config, "activity", "recent_event_items", default=10)
    full_stream_limit = _config_int(config, "activity", "full_stream_limit", default=200)
    event_scan_limit = _config_int(config, "activity", "event_scan_limit", default=1200)
    activity: dict[str, dict[str, Any]] = {
        agent.id: {"output": [], "full_output": [], "tools": [], "events": []}
        for agent in agents
    }
    for event in events[-event_scan_limit:]:
        if event.actor_id not in activity:
            continue
        agent_activity = activity[event.actor_id]
        if event.event_type in {"worker_output", "agent_output"}:
            item = _agent_output_item(event)
            agent_activity["output"].append(item)
            agent_activity["output"] = _tail(agent_activity["output"], output_limit)
            agent_activity["full_output"].append(item)
            agent_activity["full_output"] = _tail(agent_activity["full_output"], full_stream_limit)
            continue
        if event.event_type.startswith("mcp_tool_call_"):
            agent_activity["tools"].append(_tool_event_item(event))
            agent_activity["tools"] = _tail(agent_activity["tools"], tool_limit)
            continue
        if event.event_type in {
            "task_created",
            "task_assigned",
            "task_status_updated",
            "discussion_message",
            "report_registered",
            "manager_review_created",
            "manager_review_decided",
            "progress_checked",
            "agent_hired",
            "system_prompt_updated",
        }:
            agent_activity["events"].append(_activity_event_item(event))
            agent_activity["events"] = _tail(agent_activity["events"], event_limit)
    by_agent = {agent.id: agent for agent in agents}
    for agent_id, agent_activity in activity.items():
        agent_activity["summary"] = _agent_activity_summary(by_agent[agent_id], agent_activity, config)
    return activity


def _org_chart(agents: list[Any], activity: dict[str, dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    by_manager: dict[str | None, list[Any]] = {}
    for agent in agents:
        by_manager.setdefault(agent.manager_id, []).append(agent)

    def build(agent: Any) -> dict[str, Any]:
        children = [build(child) for child in sorted(by_manager.get(agent.id, []), key=lambda item: item.id)]
        descendant_count = sum(1 + int(child.get("descendant_count", 0)) for child in children)
        return {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "department": agent.department,
            "status": agent.status,
            "model": agent.model,
            "model_capabilities": model_capabilities(agent.model) or {},
            "worker_type": agent.worker_type,
            "icon": _agent_icon(agent, config),
            "current_task_ids": list(agent.current_task_ids),
            "activity": activity.get(agent.id, {"output": [], "full_output": [], "tools": [], "events": []}),
            "summary": activity.get(agent.id, {}).get("summary", {}),
            "child_count": len(children),
            "descendant_count": descendant_count,
            "children": children,
        }

    return [build(agent) for agent in sorted(by_manager.get(None, []), key=lambda item: item.id)]


def _agent_activity_summary(agent: Any, activity: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    max_chars = _config_int(config, "summaries", "max_chars", default=140)
    items: list[dict[str, str]] = []
    for output in activity.get("full_output", [])[-1:]:
        text = _single_line(str(output.get("text") or ""), max_chars)
        if text:
            stream = output.get("stream") or "output"
            items.append(
                {
                    "timestamp": str(output.get("timestamp") or ""),
                    "event_type": str(output.get("event_type") or "output"),
                    "text": f"{stream}: {text}",
                    "task_id": str(output.get("task_id") or ""),
                }
            )
    for tool in activity.get("tools", [])[-1:]:
        status = str(tool.get("status") or "call")
        tool_name = str(tool.get("tool_name") or "tool")
        target = f" -> {tool['target_agent_id']}" if tool.get("target_agent_id") else ""
        if status == "started":
            text = f"Using {tool_name}{target}"
        elif status == "finished":
            text = f"Finished {tool_name}{target}"
        elif status == "failed":
            detail = _single_line(str(tool.get("message") or ""), max_chars)
            text = f"Tool {tool_name} failed: {detail}" if detail else f"Tool {tool_name} failed"
        else:
            text = f"{status} {tool_name}{target}"
        items.append(
            {
                "timestamp": str(tool.get("timestamp") or ""),
                "event_type": str(tool.get("event_type") or ""),
                "text": _single_line(text, max_chars),
                "task_id": str(tool.get("task_id") or ""),
            }
        )
    for event in activity.get("events", [])[-1:]:
        detail = _single_line(str(event.get("detail") or ""), max_chars)
        text = str(event.get("event_type") or "event")
        if detail:
            text = f"{text}: {detail}"
        items.append(
            {
                "timestamp": str(event.get("timestamp") or ""),
                "event_type": str(event.get("event_type") or ""),
                "text": _single_line(text, max_chars),
                "task_id": str(event.get("task_id") or ""),
            }
        )

    latest = max(items, key=lambda item: item["timestamp"], default=None)
    active = bool(agent.current_task_ids) or agent.status in {"busy", "blocked"}
    if latest is not None:
        text = latest["text"]
        task_id = latest["task_id"]
        event_type = latest["event_type"]
        timestamp = latest["timestamp"]
    elif agent.current_task_ids:
        text = f"Working on {', '.join(agent.current_task_ids)}"
        task_id = agent.current_task_ids[0]
        event_type = ""
        timestamp = ""
    elif agent.status == "idle":
        text = "Idle."
        task_id = ""
        event_type = ""
        timestamp = ""
    else:
        text = f"{agent.status}."
        task_id = ""
        event_type = ""
        timestamp = ""
    return {
        "mode": "local",
        "requested_mode": str(config.get("summaries", {}).get("mode") or "local"),
        "text": _single_line(text, max_chars),
        "task_id": task_id,
        "event_type": event_type,
        "updated_at": timestamp,
        "active": active,
    }


def _config_int(config: dict[str, Any], section: str, key: str, *, default: int) -> int:
    try:
        value = int(config.get(section, {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return max(value, 1)


def _tail(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    return items[-limit:]


def _agent_icon(agent: Any, config: dict[str, Any]) -> dict[str, str]:
    worker_type = str(agent.worker_type or "").lower()
    role = str(agent.role or "").lower()
    model = str(agent.model or "").lower()
    if "codex" in worker_type or "codex" in role:
        icon = _configured_icon(config, "codex", fallback_label="Codex")
        if icon["image_url"] == "/assets/agent-icons/codex.png" and not CODEX_ICON_PATH.exists():
            icon["image_url"] = ""
        return icon
    if "claude" in worker_type or "claude" in role or "claude" in model:
        return _configured_icon(config, "claude", fallback_label="Claude")
    if "laguna" in model or "poolside" in model:
        return _configured_icon(config, "poolside", fallback_label="Pool")
    if "manager" in role or "vp" in role:
        return _configured_icon(config, "manager", fallback_label="Mgr")
    if "ceo" in role:
        return _configured_icon(config, "executive", fallback_label="CEO")
    icon = _configured_icon(config, "generic", fallback_label=worker_type[:3].upper() if worker_type else "AI")
    icon["kind"] = worker_type or "agent"
    return icon


def _configured_icon(config: dict[str, Any], kind: str, *, fallback_label: str) -> dict[str, str]:
    icon = config.get("icons", {}).get(kind, {})
    return {
        "kind": kind,
        "label": str(icon.get("label") or fallback_label),
        "image_url": str(icon.get("image_url") or ""),
    }


def _agent_output_item(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "task_id": event.task_id,
        "agent_id": event.actor_id,
        "run_id": event.payload.get("run_id"),
        "stream": event.payload.get("stream"),
        "text": event.payload.get("text"),
    }


def _tool_event_item(event: Any) -> dict[str, Any]:
    payload = event.payload
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "task_id": event.task_id,
        "agent_id": event.actor_id,
        "tool_name": payload.get("tool_name"),
        "status": event.event_type.removeprefix("mcp_tool_call_"),
        "target_agent_id": payload.get("target_agent_id")
        or payload.get("to_agent_id")
        or payload.get("assigned_to")
        or payload.get("worker_id"),
        "message": payload.get("message") or payload.get("title") or payload.get("error") or "",
        "result_id": payload.get("task_id") or payload.get("report_id") or payload.get("event_id") or "",
    }


def _activity_event_item(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "task_id": event.task_id,
        "agent_id": event.actor_id,
        "detail": _compact_event_detail(event),
    }


def _event_summary(event: Any) -> dict[str, Any]:
    payload = dict(event.payload)
    if event.event_type in {"worker_output", "agent_output"} and "text" in payload:
        payload["text"] = _single_line(str(payload["text"]), 240)
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "actor_id": event.actor_id,
        "task_id": event.task_id,
        "payload": payload,
    }


def _sequenced_event_summary(item: SequencedEvent) -> dict[str, Any]:
    return {"sequence": item.sequence, "event": _event_summary(item.event)}


def _compact_event_replay(events: list[Any]) -> str:
    lines = [
        "Event Replay",
        "============",
    ]
    if not events:
        lines.append("No events.")
        return "\n".join(lines)
    for index, event in enumerate(events, start=1):
        target = f" task={event.task_id}" if event.task_id else ""
        detail = _compact_event_detail(event)
        suffix = f" {detail}" if detail else ""
        lines.append(f"{index:02d}. {event.event_type}{target} actor={event.actor_id}{suffix}")
    return "\n".join(lines)


def _compact_event_detail(event: Any) -> str:
    payload = event.payload
    keys = [
        "tool_name",
        "assigned_to",
        "to_agent_id",
        "target_agent_id",
        "status",
        "stream",
        "returncode",
        "timed_out",
        "report_id",
        "decision",
        "message",
    ]
    parts: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        parts.append(f"{key}={_single_line(str(value), 120)}")
    if event.event_type in {"worker_output", "agent_output"}:
        text = _single_line(str(payload.get("text", "")), 160)
        if text:
            parts.append(f"text={text}")
    return " ".join(parts)


def _single_line(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Workforce Runtime Dashboard</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --line: #d8dde5;
      --text: #17202a;
      --muted: #64748b;
      --accent: #0f766e;
      --accent-weak: #e7f4f2;
      --warn: #b45309;
      --bad: #b91c1c;
      --good: #15803d;
      --shadow: 0 18px 60px rgba(15, 23, 42, 0.16);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body.detail-open { overflow: hidden; }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1, h2, h3 { margin: 0; }
    h1 { font-size: 18px; }
    h2 { font-size: 13px; text-transform: uppercase; color: var(--muted); letter-spacing: 0; }
    h3 { font-size: 14px; }
    button {
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--text);
      border-radius: 7px;
      padding: 6px 9px;
      cursor: pointer;
      font: inherit;
    }
    button:hover { border-color: var(--accent); color: var(--accent); }
    main { padding: 16px; display: grid; gap: 12px; }
    .grid { display: grid; gap: 12px; grid-template-columns: repeat(12, 1fr); }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-width: 0;
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-6 { grid-column: span 6; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .metric { font-size: 22px; font-weight: 650; margin-top: 6px; }
    .muted { color: var(--muted); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 7px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; font-size: 12px; }
    .status { font-weight: 650; }
    .completed, .idle, .finished { color: var(--good); }
    .failed, .timed_out { color: var(--bad); }
    .busy, .assigned, .in_progress, .running, .blocked { color: var(--warn); }
    pre {
      margin: 8px 0 0;
      padding: 10px;
      background: #0f172a;
      color: #e2e8f0;
      border-radius: 6px;
      overflow: auto;
      max-height: 340px;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
    }
    .output-line {
      border-bottom: 1px solid var(--line);
      padding: 6px 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .pill {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      color: var(--muted);
      font-size: 12px;
    }
    .org-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }
    .org-tree, .org-children {
      list-style: none;
      padding-left: 0;
      margin: 0;
    }
    .org-children {
      margin-left: 18px;
      padding-left: 14px;
      border-left: 1px solid var(--line);
    }
    .org-node { margin: 10px 0; }
    .org-placeholder {
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--muted);
      padding: 8px 10px;
      background: #fbfcfd;
    }
    .agent-node {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .agent-node.active { border-color: #94d2c9; background: #fcfffe; }
    .agent-node-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
    }
    .agent-title {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .agent-controls {
      display: flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
    }
    .tree-toggle {
      width: 30px;
      height: 30px;
      padding: 0;
      font-weight: 800;
      line-height: 1;
    }
    .agent-icon, .agent-icon-fallback {
      width: 28px;
      height: 28px;
      border-radius: 7px;
      flex: 0 0 auto;
    }
    .agent-icon {
      object-fit: cover;
      border: 1px solid var(--line);
      background: #fff;
    }
    .agent-icon-fallback {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      background: #e8eef6;
      color: #243449;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0;
    }
    .agent-name { font-weight: 700; }
    .agent-meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    .agent-summary {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      padding: 8px;
      border-radius: 7px;
      background: #eef3f7;
      color: #1f2f3f;
    }
    .agent-summary.active { background: var(--accent-weak); }
    .summary-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--muted);
      flex: 0 0 auto;
    }
    .agent-summary.active .summary-dot { background: var(--accent); }
    .summary-text {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .activity-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .activity-block {
      min-width: 0;
      border-top: 1px solid var(--line);
      padding-top: 6px;
    }
    .activity-title {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      margin-bottom: 4px;
    }
    .activity-item {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      color: #334155;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border-bottom: 1px solid #edf0f4;
      padding: 3px 0;
    }
    .detail-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.28);
      z-index: 5;
    }
    .detail-drawer {
      position: fixed;
      top: 0;
      right: 0;
      height: 100vh;
      width: min(760px, 94vw);
      background: var(--panel);
      border-left: 1px solid var(--line);
      box-shadow: var(--shadow);
      z-index: 6;
      transform: translateX(105%);
      transition: transform 160ms ease;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    body.detail-open .detail-drawer { transform: translateX(0); }
    .detail-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .detail-body {
      overflow: auto;
      padding: 14px 16px 28px;
      display: grid;
      gap: 12px;
    }
    .detail-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
    }
    .detail-section h3 {
      margin-bottom: 8px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
      font-size: 12px;
    }
    .stream-box {
      max-height: 360px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      background: #fcfdff;
    }
    @media (max-width: 900px) {
      .span-3, .span-4, .span-6, .span-8 { grid-column: span 12; }
      .activity-grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; gap: 4px; }
      .agent-node-head { flex-direction: column; }
      .agent-controls { width: 100%; justify-content: space-between; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Workforce Runtime</h1>
      <div class="muted" id="mission"></div>
    </div>
    <div class="muted">Stream <span id="stream-status">connecting</span> - State <span id="updated">loading</span></div>
  </header>
  <main>
    <section class="grid" id="metrics"></section>
    <section class="grid">
      <div class="panel span-12">
        <div class="org-toolbar">
          <h2>Org Chart</h2>
          <div class="muted" id="org-summary"></div>
        </div>
        <div id="org-chart"></div>
      </div>
      <div class="panel span-6">
        <h2>Agents</h2>
        <table id="agents"></table>
      </div>
      <div class="panel span-6">
        <h2>Tasks</h2>
        <table id="tasks"></table>
      </div>
      <div class="panel span-6">
        <h2>Agent Runs</h2>
        <table id="runs"></table>
      </div>
      <div class="panel span-6">
        <h2>Reports</h2>
        <table id="reports"></table>
      </div>
      <div class="panel span-12">
        <h2>Live Agent Output</h2>
        <div id="output"></div>
      </div>
      <div class="panel span-6">
        <h2>Replay</h2>
        <pre id="replay"></pre>
      </div>
      <div class="panel span-6">
        <h2>Trajectories</h2>
        <pre id="trajectories"></pre>
      </div>
    </section>
  </main>
  <div class="detail-backdrop" id="agent-backdrop" hidden data-action="close-detail"></div>
  <aside class="detail-drawer" id="agent-detail" aria-hidden="true"></aside>
  <script>
    const DEFAULT_CONFIG = {
      dashboard: { refresh_interval_ms: 5000, max_visible_agents: 80, collapse_depth: 3, show_idle_activity: true },
      activity: { recent_output_items: 12, recent_tool_items: 12, recent_event_items: 10, full_stream_limit: 200, global_output_limit: 200 },
      summaries: { mode: "local", max_chars: 140 }
    };
    const ACTIVITY_EVENT_TYPES = new Set([
      "task_created",
      "task_assigned",
      "task_status_updated",
      "discussion_message",
      "report_registered",
      "manager_review_created",
      "manager_review_decided",
      "progress_checked",
      "agent_hired",
      "system_prompt_updated",
    ]);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const statusClass = (value) => esc(value).replace(/[^a-zA-Z0-9_-]/g, "_");
    const rows = (headers, values) => {
      const head = `<tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr>`;
      const body = values.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("");
      return head + body;
    };
    let eventCursor = 0;
    let eventSource = null;
    let liveOutput = [];
    let orgChart = [];
    let agents = [];
    let agentActivity = {};
    let dashboardConfig = structuredClone(DEFAULT_CONFIG);
    let expandedNodes = new Set();
    let collapsedNodes = new Set();
    let selectedAgentId = null;
    let visibleNodeCount = 0;

    function deepMerge(base, override) {
      const result = {...base};
      for (const [key, value] of Object.entries(override || {})) {
        if (value && typeof value === "object" && !Array.isArray(value) && base[key] && typeof base[key] === "object") {
          result[key] = deepMerge(base[key], value);
        } else {
          result[key] = value;
        }
      }
      return result;
    }

    function cfg(section, key, fallback) {
      return dashboardConfig?.[section]?.[key] ?? fallback;
    }

    function clip(value, limit = cfg("summaries", "max_chars", 140)) {
      const text = String(value ?? "").replace(/\s+/g, " ").trim();
      return text.length > limit ? `${text.slice(0, Math.max(limit - 3, 1))}...` : text;
    }

    function renderOutput() {
      document.getElementById("output").innerHTML = liveOutput.slice(-cfg("activity", "global_output_limit", 200)).map(o =>
        `<div class="output-line"><span class="pill">${esc(o.task_id || "-")} ${esc(o.agent_id)} ${esc(o.stream || "output")}</span> ${esc(o.text || "")}</div>`
      ).join("") || `<div class="muted">No agent output.</div>`;
    }

    function renderOrgChart() {
      visibleNodeCount = 0;
      const agentCount = agents.length || countNodes(orgChart);
      document.getElementById("org-summary").textContent = `${agentCount} agents - collapse depth ${cfg("dashboard", "collapse_depth", 3)}`;
      document.getElementById("org-chart").innerHTML = orgChart.length
        ? `<ul class="org-tree">${orgChart.map(node => renderOrgNode(node, 0)).join("")}</ul>`
        : `<div class="muted">No agents.</div>`;
    }

    function renderOrgNode(node, depth) {
      const maxVisible = cfg("dashboard", "max_visible_agents", 80);
      if (visibleNodeCount >= maxVisible && depth > 0) {
        const total = 1 + Number(node.descendant_count || 0);
        return `<li class="org-node"><div class="org-placeholder">${esc(node.name)} and ${esc(total)} agent(s) hidden by display limit.</div></li>`;
      }
      visibleNodeCount += 1;
      const activity = ensureAgentActivity(node.id, node.activity);
      const summary = activity.summary || node.summary || summarizeActivity(activity, node);
      const active = Boolean(summary.active || (node.current_task_ids || []).length || node.status === "busy" || node.status === "blocked");
      const work = (node.current_task_ids || []).join(", ") || "-";
      const children = node.children || [];
      const hasChildren = children.length > 0;
      const collapsed = isNodeCollapsed(node, depth);
      const toggle = hasChildren
        ? `<button class="tree-toggle" data-action="toggle-node" data-agent-id="${esc(node.id)}" data-depth="${esc(depth)}" title="Toggle reports">${collapsed ? "+" : "-"}</button>`
        : "";
      const childrenMarkup = hasChildren
        ? collapsed
          ? `<ul class="org-children"><li class="org-node"><div class="org-placeholder">${esc(children.length)} direct report(s), ${esc(node.descendant_count || children.length)} total below.</div></li></ul>`
          : `<ul class="org-children">${children.map(child => renderOrgNode(child, depth + 1)).join("")}</ul>`
        : "";
      return `<li class="org-node">
        <div class="agent-node ${active ? "active" : ""}" data-agent-id="${esc(node.id)}">
          <div class="agent-node-head">
            <div class="agent-title">
              ${renderAgentIcon(node.icon)}
              <div>
                <div class="agent-name">${esc(node.name)}</div>
                <div class="agent-meta">${esc(node.role)} - ${esc(node.worker_type)} - ${esc(node.model || "no model")}</div>
                <div class="agent-meta">${esc(renderModelLimit(node.model_capabilities))}</div>
                <div class="agent-meta">tasks: ${esc(work)}</div>
              </div>
            </div>
            <div class="agent-controls">
              ${toggle}
              <button data-action="agent-detail" data-agent-detail="${esc(node.id)}">Details</button>
              <span class="status ${statusClass(node.status)}">${esc(node.status)}</span>
            </div>
          </div>
          <div class="agent-summary ${active ? "active" : ""}">
            <span class="summary-dot"></span>
            <span class="summary-text">${esc(summary.text || "Idle.")}</span>
          </div>
          <div class="activity-grid">
            ${renderActivityBlock("Output", activity.output, renderOutputItem)}
            ${renderActivityBlock("Tools", activity.tools, renderToolItem)}
            ${renderActivityBlock("Events", activity.events, renderEventItem)}
          </div>
        </div>
        ${childrenMarkup}
      </li>`;
    }

    function isNodeCollapsed(node, depth) {
      if (!(node.children || []).length) return false;
      if (expandedNodes.has(node.id)) return false;
      if (collapsedNodes.has(node.id)) return true;
      return depth >= cfg("dashboard", "collapse_depth", 3);
    }

    function countNodes(nodes) {
      return (nodes || []).reduce((total, node) => total + 1 + countNodes(node.children || []), 0);
    }

    function renderAgentIcon(icon) {
      const label = esc(icon?.label || "AI");
      if (icon?.image_url) {
        return `<img class="agent-icon" src="${esc(icon.image_url)}" alt="${label}" title="${label}" onerror="this.outerHTML='<span class=&quot;agent-icon-fallback&quot; title=&quot;${label}&quot;>${label.slice(0, 3)}</span>'">`;
      }
      return `<span class="agent-icon-fallback" title="${label}">${label.slice(0, 3)}</span>`;
    }

    function renderModelLimit(capabilities) {
      const context = capabilities?.context_window_tokens;
      const output = capabilities?.max_output_tokens;
      if (!context && !output) return "model limits: unknown";
      const parts = [];
      if (context) parts.push(`context ${Number(context).toLocaleString()} tokens`);
      if (output) parts.push(`output ${Number(output).toLocaleString()} tokens`);
      return `model limits: ${parts.join(", ")}`;
    }

    function renderActivityBlock(title, items, renderItem) {
      const body = (items || []).slice(-5).map(renderItem).join("") || `<div class="muted">None.</div>`;
      return `<div class="activity-block"><div class="activity-title">${esc(title)}</div>${body}</div>`;
    }

    function renderOutputItem(item) {
      return `<div class="activity-item">${esc(item.stream || "output")}: ${esc(item.text || "")}</div>`;
    }

    function renderToolItem(item) {
      const target = item.target_agent_id ? ` -> ${item.target_agent_id}` : "";
      const result = item.result_id ? ` ${item.result_id}` : "";
      return `<div class="activity-item">${esc(item.status || "call")} ${esc(item.tool_name || "tool")}${esc(target)}${esc(result)} ${esc(item.message || "")}</div>`;
    }

    function renderEventItem(item) {
      return `<div class="activity-item">${esc(item.event_type || "event")} ${esc(item.task_id || "")} ${esc(item.detail || "")}</div>`;
    }

    function ensureAgentActivity(agentId, fallback = null) {
      if (!agentActivity[agentId]) {
        agentActivity[agentId] = fallback || { output: [], full_output: [], tools: [], events: [] };
      }
      if (!agentActivity[agentId].output) agentActivity[agentId].output = [];
      if (!agentActivity[agentId].full_output) agentActivity[agentId].full_output = [...agentActivity[agentId].output];
      if (!agentActivity[agentId].tools) agentActivity[agentId].tools = [];
      if (!agentActivity[agentId].events) agentActivity[agentId].events = [];
      return agentActivity[agentId];
    }

    function summarizeActivity(activity, node = null) {
      if (activity?.summary?.text) return activity.summary;
      const candidates = [];
      const output = (activity.full_output || activity.output || []).slice(-1)[0];
      if (output) {
        candidates.push({
          timestamp: output.timestamp || "",
          text: `${output.stream || "output"}: ${clip(output.text || "")}`,
          task_id: output.task_id || "",
          event_type: output.event_type || "output",
        });
      }
      const tool = (activity.tools || []).slice(-1)[0];
      if (tool) {
        const target = tool.target_agent_id ? ` -> ${tool.target_agent_id}` : "";
        const verb = tool.status === "started" ? "Using" : tool.status === "finished" ? "Finished" : tool.status || "Tool";
        candidates.push({
          timestamp: tool.timestamp || "",
          text: clip(`${verb} ${tool.tool_name || "tool"}${target}`),
          task_id: tool.task_id || "",
          event_type: tool.event_type || "",
        });
      }
      const event = (activity.events || []).slice(-1)[0];
      if (event) {
        candidates.push({
          timestamp: event.timestamp || "",
          text: clip(`${event.event_type || "event"} ${event.detail || ""}`),
          task_id: event.task_id || "",
          event_type: event.event_type || "",
        });
      }
      candidates.sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
      const latest = candidates[candidates.length - 1];
      if (latest) {
        return {...latest, mode: "local", active: Boolean((node?.current_task_ids || []).length || node?.status === "busy" || node?.status === "blocked")};
      }
      if ((node?.current_task_ids || []).length) {
        return {mode: "local", text: `Working on ${(node.current_task_ids || []).join(", ")}`, active: true};
      }
      return {mode: "local", text: "Idle.", active: false};
    }

    function compactEventDetail(event) {
      const payload = event.payload || {};
      const keys = ["tool_name", "assigned_to", "to_agent_id", "target_agent_id", "status", "stream", "returncode", "timed_out", "report_id", "decision", "message"];
      return keys.filter(key => payload[key] != null).map(key => `${key}=${clip(payload[key], 120)}`).join(" ");
    }

    function appendAgentEvent(event) {
      if (!event.actor_id) return;
      const activity = ensureAgentActivity(event.actor_id);
      if (event.event_type === "worker_output" || event.event_type === "agent_output") {
        const item = {
          event_id: event.event_id,
          event_type: event.event_type,
          timestamp: event.timestamp,
          task_id: event.task_id,
          agent_id: event.actor_id,
          run_id: event.payload?.run_id,
          stream: event.payload?.stream,
          text: event.payload?.text,
        };
        liveOutput.push(item);
        activity.output.push(item);
        activity.full_output.push(item);
        activity.output = activity.output.slice(-cfg("activity", "recent_output_items", 12));
        activity.full_output = activity.full_output.slice(-cfg("activity", "full_stream_limit", 200));
      } else if ((event.event_type || "").startsWith("mcp_tool_call_")) {
        activity.tools.push({
          event_id: event.event_id,
          timestamp: event.timestamp,
          event_type: event.event_type,
          task_id: event.task_id,
          agent_id: event.actor_id,
          tool_name: event.payload?.tool_name,
          status: (event.event_type || "").replace("mcp_tool_call_", ""),
          target_agent_id: event.payload?.target_agent_id || event.payload?.to_agent_id || event.payload?.assigned_to || event.payload?.worker_id,
          message: event.payload?.message || event.payload?.title || event.payload?.error || "",
          result_id: event.payload?.task_id || event.payload?.report_id || event.payload?.event_id || "",
        });
        activity.tools = activity.tools.slice(-cfg("activity", "recent_tool_items", 12));
      } else if (ACTIVITY_EVENT_TYPES.has(event.event_type || "")) {
        activity.events.push({
          event_id: event.event_id,
          timestamp: event.timestamp,
          event_type: event.event_type,
          task_id: event.task_id,
          agent_id: event.actor_id,
          detail: compactEventDetail(event),
        });
        activity.events = activity.events.slice(-cfg("activity", "recent_event_items", 10));
      } else {
        return;
      }
      activity.summary = summarizeActivity(activity, findNodeById(event.actor_id));
      renderOutput();
      renderOrgChart();
      if (selectedAgentId === event.actor_id) renderAgentDetail();
    }

    function findNodeById(agentId, nodes = orgChart) {
      for (const node of nodes || []) {
        if (node.id === agentId) return node;
        const found = findNodeById(agentId, node.children || []);
        if (found) return found;
      }
      return null;
    }

    function renderAgentDetail() {
      const drawer = document.getElementById("agent-detail");
      const backdrop = document.getElementById("agent-backdrop");
      if (!selectedAgentId) {
        drawer.setAttribute("aria-hidden", "true");
        drawer.innerHTML = "";
        backdrop.hidden = true;
        document.body.classList.remove("detail-open");
        return;
      }
      const node = findNodeById(selectedAgentId) || agents.find(agent => agent.id === selectedAgentId) || {id: selectedAgentId, name: selectedAgentId, role: "", status: ""};
      const activity = ensureAgentActivity(selectedAgentId, node.activity);
      const summary = summarizeActivity(activity, node);
      const work = (node.current_task_ids || []).join(", ") || "-";
      drawer.setAttribute("aria-hidden", "false");
      backdrop.hidden = false;
      document.body.classList.add("detail-open");
      drawer.innerHTML = `
        <div class="detail-head">
          <div>
            <h3>${esc(node.name || node.id)}</h3>
            <div class="muted">${esc(node.role || "")} - ${esc(node.worker_type || "")} - <span class="status ${statusClass(node.status)}">${esc(node.status || "")}</span></div>
          </div>
          <button data-action="close-detail">Close</button>
        </div>
        <div class="detail-body">
          <div class="detail-section">
            <h3>Current Summary</h3>
            <div class="agent-summary ${summary.active ? "active" : ""}"><span class="summary-dot"></span><span class="summary-text">${esc(summary.text || "Idle.")}</span></div>
            <div class="agent-meta">tasks: ${esc(work)} - summary mode: ${esc(summary.mode || "local")}</div>
          </div>
          <div class="detail-section">
            <h3>Full Stream</h3>
            <div class="stream-box">${(activity.full_output || activity.output || []).map(renderOutputItem).join("") || `<div class="muted output-line">No stream output.</div>`}</div>
          </div>
          <div class="detail-section">
            <h3>Tool Calls</h3>
            ${(activity.tools || []).map(renderToolItem).join("") || `<div class="muted">No tool calls.</div>`}
          </div>
          <div class="detail-section">
            <h3>Events</h3>
            ${(activity.events || []).map(renderEventItem).join("") || `<div class="muted">No events.</div>`}
          </div>
        </div>`;
    }

    function connectStream() {
      if (eventSource || !window.EventSource) return;
      eventSource = new EventSource(`/api/events/stream?after=${eventCursor}`);
      eventSource.addEventListener("open", () => {
        document.getElementById("stream-status").textContent = "live";
      });
      eventSource.addEventListener("runtime_event", (message) => {
        const item = JSON.parse(message.data);
        eventCursor = Math.max(eventCursor, item.sequence || 0);
        appendAgentEvent(item.event || {});
      });
      eventSource.addEventListener("heartbeat", (message) => {
        const item = JSON.parse(message.data);
        eventCursor = Math.max(eventCursor, item.cursor || 0);
      });
      eventSource.onerror = () => {
        document.getElementById("stream-status").textContent = "reconnecting";
      };
    }

    async function refresh() {
      const res = await fetch("/api/state", { cache: "no-store" });
      const data = await res.json();
      dashboardConfig = deepMerge(DEFAULT_CONFIG, data.config || {});
      eventCursor = Math.max(eventCursor, data.cursor || 0);
      liveOutput = data.agent_output || data.worker_output || [];
      orgChart = data.org_chart || [];
      agents = data.agents || [];
      agentActivity = data.agent_activity || {};
      document.getElementById("mission").textContent = `${data.company.name} - ${data.company.mission || "No mission"}`;
      document.getElementById("updated").textContent = new Date().toLocaleTimeString();
      const active = data.tasks.filter(t => ["assigned", "in_progress", "blocked"].includes(t.status)).length;
      const completed = data.tasks.filter(t => t.status === "completed").length;
      const failed = data.tasks.filter(t => t.status === "failed").length;
      document.getElementById("metrics").innerHTML = [
        ["Agents", `${data.agents.length}${data.budget.headcount_limit ? " / " + data.budget.headcount_limit : ""}`],
        ["Active Tasks", active],
        ["Completed", completed],
        ["Failed", failed],
        ["Tokens", `${data.budget.tokens_used} / ${data.budget.token_budget_limit}`],
        ["Output Events", liveOutput.length],
      ].map(([label, value]) => `<div class="panel span-3"><h2>${esc(label)}</h2><div class="metric">${esc(value)}</div></div>`).join("");
      document.getElementById("agents").innerHTML = rows(["Agent", "Role", "Status", "Model", "Current Work"], data.agents.map(a => [
        `<button data-action="agent-detail" data-agent-detail="${esc(a.id)}">${esc(a.name)}</button>`,
        esc(a.role),
        `<span class="status ${statusClass(a.status)}">${esc(a.status)}</span>`,
        esc(a.model || "-"),
        esc((a.current_task_ids || []).join(", ") || "-")
      ]));
      renderOrgChart();
      document.getElementById("tasks").innerHTML = rows(["Task", "Title", "Status", "Assignee"], data.tasks.map(t => [
        esc(t.task_id),
        esc(t.title),
        `<span class="status ${statusClass(t.status)}">${esc(t.status)}</span>`,
        esc(t.assigned_to || "-")
      ]));
      document.getElementById("runs").innerHTML = rows(["Run", "Task", "Agent", "Kind", "Status", "Runtime"], (data.agent_runs || data.worker_runs).map(r => [
        esc(r.run_id),
        esc(r.task_id || "-"),
        esc(r.agent_id),
        esc(r.kind || "worker"),
        `<span class="status ${statusClass(r.status)}">${esc(r.status)}${r.returncode != null ? " " + esc(r.returncode) : ""}</span>`,
        esc(r.executable || r.adapter || r.model || "-")
      ]));
      document.getElementById("reports").innerHTML = rows(["Report", "From", "To", "Task", "Status", "Summary"], data.reports.map(r => [
        esc(r.report_id),
        esc(r.from_agent_id),
        esc(r.to_agent_id),
        esc(r.task_id),
        esc(r.status),
        esc(r.summary).slice(0, 220)
      ]));
      renderOutput();
      renderAgentDetail();
      document.getElementById("replay").textContent = data.event_replay;
      document.getElementById("trajectories").textContent = data.trajectories;
      connectStream();
    }

    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action]");
      if (!target) return;
      const action = target.dataset.action;
      if (action === "agent-detail") {
        selectedAgentId = target.dataset.agentDetail;
        renderAgentDetail();
      }
      if (action === "close-detail") {
        selectedAgentId = null;
        renderAgentDetail();
      }
      if (action === "toggle-node") {
        const agentId = target.dataset.agentId;
        const depth = Number(target.dataset.depth || 0);
        const node = findNodeById(agentId);
        if (!node) return;
        if (isNodeCollapsed(node, depth)) {
          expandedNodes.add(agentId);
          collapsedNodes.delete(agentId);
        } else {
          collapsedNodes.add(agentId);
          expandedNodes.delete(agentId);
        }
        renderOrgChart();
      }
    });

    refresh().catch(err => console.error(err));
    setInterval(() => refresh().catch(err => console.error(err)), cfg("dashboard", "refresh_interval_ms", 5000));
  </script>
</body>
</html>
"""
