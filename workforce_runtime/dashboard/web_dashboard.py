from __future__ import annotations

import argparse
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workforce_runtime.core.organization import Company
from workforce_runtime.dashboard.summaries import total_budget_usage, worker_performance
from workforce_runtime.dashboard.text_dashboard import render_agent_trajectories
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage.sqlite_store import SequencedEvent
from workforce_runtime.storage import SQLiteStore


CODEX_ICON_PATH = Path("/Applications/Codex.app/Contents/Resources/icon-codex-light.png")


def build_web_dashboard_state(store: SQLiteStore) -> dict[str, Any]:
    company = store.get_company() or Company(name="Unknown Workforce")
    agents = store.list_agents()
    tasks = store.list_tasks()
    reports = store.list_reports()
    artifacts = store.list_artifacts()
    events = store.list_events()
    budget = total_budget_usage(tasks, reports)

    recent_events = events[-300:]
    output = _agent_output(events)
    runs = _agent_runs(events)
    activity = _agent_activity(agents, events)
    return {
        "cursor": store.latest_event_sequence(),
        "company": company.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "org_chart": _org_chart(agents, activity),
        "agent_activity": activity,
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
        "agent_output": output[-200:],
        "worker_output": output[-80:],
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
) -> None:
    server = make_web_dashboard_server(db_path, host=host, port=port)
    address = server.server_address
    print(f"Workforce Runtime web dashboard: http://{address[0]}:{address[1]}", flush=True)
    server.serve_forever()


def make_web_dashboard_server(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    db = Path(db_path)

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
                    state = build_web_dashboard_state(runtime.store)
                self._send_json(state)
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


def _agent_activity(agents: list[Any], events: list[Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    activity: dict[str, dict[str, list[dict[str, Any]]]] = {
        agent.id: {"output": [], "tools": [], "events": []}
        for agent in agents
    }
    for event in events[-800:]:
        if event.actor_id not in activity:
            continue
        agent_activity = activity[event.actor_id]
        if event.event_type in {"worker_output", "agent_output"}:
            agent_activity["output"].append(_agent_output_item(event))
            agent_activity["output"] = agent_activity["output"][-12:]
            continue
        if event.event_type.startswith("mcp_tool_call_"):
            agent_activity["tools"].append(_tool_event_item(event))
            agent_activity["tools"] = agent_activity["tools"][-12:]
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
            agent_activity["events"] = agent_activity["events"][-10:]
    return activity


def _org_chart(agents: list[Any], activity: dict[str, dict[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    by_manager: dict[str | None, list[Any]] = {}
    for agent in agents:
        by_manager.setdefault(agent.manager_id, []).append(agent)

    def build(agent: Any) -> dict[str, Any]:
        return {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "department": agent.department,
            "status": agent.status,
            "model": agent.model,
            "worker_type": agent.worker_type,
            "icon": _agent_icon(agent),
            "current_task_ids": list(agent.current_task_ids),
            "activity": activity.get(agent.id, {"output": [], "tools": [], "events": []}),
            "children": [build(child) for child in sorted(by_manager.get(agent.id, []), key=lambda item: item.id)],
        }

    return [build(agent) for agent in sorted(by_manager.get(None, []), key=lambda item: item.id)]


def _agent_icon(agent: Any) -> dict[str, str]:
    worker_type = str(agent.worker_type or "").lower()
    role = str(agent.role or "").lower()
    model = str(agent.model or "").lower()
    if "codex" in worker_type or "codex" in role:
        return {
            "kind": "codex",
            "label": "Codex",
            "image_url": "/assets/agent-icons/codex.png" if CODEX_ICON_PATH.exists() else "",
        }
    if "claude" in worker_type or "claude" in role or "claude" in model:
        return {"kind": "claude", "label": "Claude", "image_url": ""}
    if "laguna" in model or "poolside" in model:
        return {"kind": "poolside", "label": "Poolside", "image_url": ""}
    if "manager" in role or "vp" in role:
        return {"kind": "manager", "label": "Mgr", "image_url": ""}
    if "ceo" in role:
        return {"kind": "executive", "label": "CEO", "image_url": ""}
    return {"kind": worker_type or "agent", "label": worker_type[:3].upper() if worker_type else "AI", "image_url": ""}


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
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --line: #d8dde5;
      --text: #17202a;
      --muted: #64748b;
      --accent: #0f766e;
      --warn: #b45309;
      --bad: #b91c1c;
      --good: #15803d;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
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
    h1, h2 { margin: 0; }
    h1 { font-size: 18px; }
    h2 { font-size: 13px; text-transform: uppercase; color: var(--muted); letter-spacing: 0; }
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
    .busy, .assigned, .in_progress, .running { color: var(--warn); }
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
    .org-node {
      margin: 10px 0;
    }
    .agent-node {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
      display: grid;
      gap: 8px;
    }
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
    .agent-name {
      font-weight: 700;
    }
    .agent-meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
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
    @media (max-width: 900px) {
      .span-3, .span-4, .span-6, .span-8 { grid-column: span 12; }
      .activity-grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; gap: 4px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Workforce Runtime</h1>
      <div class="muted" id="mission"></div>
    </div>
    <div class="muted">Stream <span id="stream-status">connecting</span> · State <span id="updated">loading</span></div>
  </header>
  <main>
    <section class="grid" id="metrics"></section>
    <section class="grid">
      <div class="panel span-12">
        <h2>Org Chart</h2>
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
  <script>
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
    let agentActivity = {};

    function renderOutput() {
      document.getElementById("output").innerHTML = liveOutput.slice(-80).map(o =>
        `<div class="output-line"><span class="pill">${esc(o.task_id || "-")} ${esc(o.agent_id)} ${esc(o.stream || "output")}</span> ${esc(o.text || "")}</div>`
      ).join("") || `<div class="muted">No agent output.</div>`;
    }

    function renderOrgChart() {
      document.getElementById("org-chart").innerHTML = orgChart.length
        ? `<ul class="org-tree">${orgChart.map(renderOrgNode).join("")}</ul>`
        : `<div class="muted">No agents.</div>`;
    }

    function renderOrgNode(node) {
      const activity = agentActivity[node.id] || node.activity || { output: [], tools: [], events: [] };
      const work = (node.current_task_ids || []).join(", ") || "-";
      const children = (node.children || []).length
        ? `<ul class="org-children">${node.children.map(renderOrgNode).join("")}</ul>`
        : "";
      return `<li class="org-node">
        <div class="agent-node" data-agent-id="${esc(node.id)}">
          <div class="agent-node-head">
            <div class="agent-title">
              ${renderAgentIcon(node.icon)}
              <div>
                <div class="agent-name">${esc(node.name)}</div>
                <div class="agent-meta">${esc(node.role)} · ${esc(node.worker_type)} · ${esc(node.model || "no model")}</div>
                <div class="agent-meta">tasks: ${esc(work)}</div>
              </div>
            </div>
            <span class="status ${statusClass(node.status)}">${esc(node.status)}</span>
          </div>
          <div class="activity-grid">
            ${renderActivityBlock("Output", activity.output, renderOutputItem)}
            ${renderActivityBlock("Tools", activity.tools, renderToolItem)}
            ${renderActivityBlock("Events", activity.events, renderEventItem)}
          </div>
        </div>
        ${children}
      </li>`;
    }

    function renderAgentIcon(icon) {
      const label = esc(icon?.label || "AI");
      if (icon?.image_url) {
        return `<img class="agent-icon" src="${esc(icon.image_url)}" alt="${label}" title="${label}" onerror="this.outerHTML='<span class=&quot;agent-icon-fallback&quot; title=&quot;${label}&quot;>${label.slice(0, 3)}</span>'">`;
      }
      return `<span class="agent-icon-fallback" title="${label}">${label.slice(0, 3)}</span>`;
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

    function ensureAgentActivity(agentId) {
      if (!agentActivity[agentId]) {
        agentActivity[agentId] = { output: [], tools: [], events: [] };
      }
      return agentActivity[agentId];
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
        activity.output = activity.output.slice(-12);
        renderOutput();
        renderOrgChart();
        return;
      }
      if ((event.event_type || "").startsWith("mcp_tool_call_")) {
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
        activity.tools = activity.tools.slice(-12);
        renderOrgChart();
      }
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
        const event = item.event || {};
        appendAgentEvent(event);
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
      eventCursor = Math.max(eventCursor, data.cursor || 0);
      liveOutput = data.agent_output || data.worker_output || [];
      orgChart = data.org_chart || [];
      agentActivity = data.agent_activity || {};
      document.getElementById("mission").textContent = `${data.company.name} · ${data.company.mission || "No mission"}`;
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
        esc(a.name),
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
      document.getElementById("replay").textContent = data.event_replay;
      document.getElementById("trajectories").textContent = data.trajectories;
      connectStream();
    }
    refresh().catch(err => console.error(err));
    setInterval(() => refresh().catch(err => console.error(err)), 5000);
  </script>
</body>
</html>
"""
