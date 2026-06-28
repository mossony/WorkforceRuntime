from __future__ import annotations

import argparse
import copy
import html as html_lib
import json
import mimetypes
import shutil
import subprocess
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

from workforce_runtime.config import (
    dashboard_config_from_runtime,
    load_runtime_config,
    merge_runtime_config,
    model_capabilities,
    runtime_config_path,
    save_runtime_config,
)
from workforce_runtime.core import Artifact
from workforce_runtime.core.organization import Company, Organization
from workforce_runtime.dashboard.config import merge_dashboard_config
from workforce_runtime.dashboard.summaries import total_budget_usage, worker_performance
from workforce_runtime.dashboard.text_dashboard import render_agent_trajectories
from workforce_runtime.evals import BenchmarkCase, load_benchmark_case, run_benchmark_case
from workforce_runtime.mcp.oauth import probe_mcp_auth, start_oauth_login_for_callback
from workforce_runtime.org_designer import OrgDesigner, OrgDesignRequest
from workforce_runtime.scheduler.dispatcher import AgentInboxDispatcher
from workforce_runtime.server.long_rfc_demo import DEFAULT_RFC_URL, run_long_rfc_demo
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import RuntimeStore, SQLiteStore, SequencedEvent
from workforce_runtime.workers import ClaudeCodeInteractiveWorker, RuntimeContext
from workforce_runtime.workers.session_resume import (
    latest_provider_session,
    queue_steer_for_resume,
    resume_provider_session,
)
from workforce_runtime.workers.steering import STEERABLE_SESSIONS


CODEX_ICON_PATH = Path("/Applications/Codex.app/Contents/Resources/icon-codex-light.png")
ELK_JS_PATH = Path(__file__).resolve().parent / "assets" / "elk.bundled.js"
DASHBOARD_STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_STATIC_ASSET_DIR = DASHBOARD_STATIC_DIR / "assets"
DASHBOARD_INDEX_PATH = DASHBOARD_STATIC_DIR / "index.html"
DASHBOARD_FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
DASHBOARD_SHELL_PATH = DASHBOARD_FRONTEND_DIR / "src" / "dashboardShell.html"
DASHBOARD_CONTROLLER_PATH = DASHBOARD_FRONTEND_DIR / "src" / "dashboardController.js"
DEFAULT_REAL_LLM_BENCHMARK_CASE = Path("examples/benchmarks/web_research_real_llm.json")


def _read_text_asset(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _dashboard_static_asset(path: str) -> Path | None:
    if not path.startswith("/assets/"):
        return None
    relative = unquote(path.removeprefix("/assets/"))
    if not relative or relative.startswith(("/", "\\")):
        return None
    candidate = (DASHBOARD_STATIC_ASSET_DIR / relative).resolve()
    root = DASHBOARD_STATIC_ASSET_DIR.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _asset_content_type(path: Path) -> str:
    if path.suffix == ".js":
        return "application/javascript; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


HTML = _read_text_asset(DASHBOARD_INDEX_PATH)
DASHBOARD_SHELL_HTML = _read_text_asset(DASHBOARD_SHELL_PATH)
DASHBOARD_CONTROLLER_JS = _read_text_asset(DASHBOARD_CONTROLLER_PATH)


def build_web_dashboard_state(
    store: RuntimeStore,
    config: dict[str, Any] | None = None,
    *,
    task_id_filter: str | None = None,
) -> dict[str, Any]:
    config = merge_dashboard_config(config)
    company = store.get_company() or Company(name="Unknown Workforce")
    all_agents = store.list_agents()
    all_tasks = store.list_tasks()
    all_reports = store.list_reports()
    all_artifacts = store.list_artifacts()
    all_task_documents = store.list_task_documents()
    all_work_items = store.list_work_items()
    all_events = store.list_events()
    task_scope = _task_filter_scope(all_tasks, task_id_filter)
    if task_scope:
        tasks = [task for task in all_tasks if task.task_id in task_scope]
        reports = [report for report in all_reports if report.task_id in task_scope]
        artifacts = [artifact for artifact in all_artifacts if artifact.task_id in task_scope]
        task_documents = [document for document in all_task_documents if document.task_id in task_scope]
        work_items = [item for item in all_work_items if item.task_id in task_scope]
        events = [event for event in all_events if _event_matches_task_scope(event, task_scope)]
        agent_ids = _agent_ids_for_task_scope(all_agents, tasks, reports, artifacts, events)
        agents = _agents_for_current_company(all_agents, company, required_agent_ids=agent_ids)
    else:
        agents = all_agents
        tasks = all_tasks
        reports = all_reports
        artifacts = all_artifacts
        task_documents = all_task_documents
        work_items = all_work_items
        events = all_events
    total_agent_count = len(agents)
    state_agent_limit = _config_int(
        config,
        "dashboard",
        "state_agent_limit",
        default=max(_config_int(config, "dashboard", "max_visible_agents", default=80) * 2, 80),
    )
    state_agents = agents if task_scope or state_agent_limit <= 0 else agents[:state_agent_limit]
    large_state = bool(not task_scope and state_agent_limit > 0 and total_agent_count > state_agent_limit)
    budget = total_budget_usage(tasks, reports)
    event_usage = _event_usage(events)
    tokens_used = budget["tokens_used"] + event_usage["actual_tokens"]
    token_estimate = event_usage["estimated_tokens"]
    if tokens_used == 0:
        tokens_used = token_estimate

    recent_event_limit = _config_int(config, "activity", "recent_event_limit", default=300)
    if large_state:
        recent_event_limit = min(recent_event_limit, 50)
    recent_events = events[-recent_event_limit:]
    output = _agent_output(events)
    runs = _agent_runs(events)
    activity = _agent_activity(state_agents, events, config)
    return {
        "cursor": store.latest_event_sequence(),
        "config": config,
        "task_filter": {
            "selected_task_id": task_id_filter or "",
            "task_ids": sorted(task_scope),
            "enabled": bool(task_scope),
        },
        "company": company.model_dump(mode="json"),
        "agent_count": total_agent_count,
        "agents_truncated": len(state_agents) < total_agent_count,
        "agents": [_agent_payload(agent) for agent in state_agents],
        "agent_profiles": [],
        "org_chart": _org_chart(agents, activity, config, max_nodes=state_agent_limit if not task_scope else 0),
        "agent_activity": activity,
        "agent_summaries": {agent_id: data.get("summary", {}) for agent_id, data in activity.items()},
        "steerable_sessions": STEERABLE_SESSIONS.list_sessions(),
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "reports": [report.model_dump(mode="json") for report in reports],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "task_documents": [document.model_dump(mode="json") for document in task_documents],
        "work_queue": _work_queue_state(work_items, item_limit=40 if large_state else 200),
        "budget": {
            **budget,
            "tokens_used": tokens_used,
            "actual_tokens_used": budget["tokens_used"] + event_usage["actual_tokens"],
            "estimated_stream_tokens": token_estimate,
            "runtime_seconds_used": budget["runtime_seconds_used"] + event_usage["runtime_seconds"],
            "tool_calls_used": budget["tool_calls_used"] + event_usage["tool_calls"],
            "token_budget_limit": company.token_budget or budget["max_tokens"],
            "headcount_limit": company.headcount_limit or None,
        },
        "agent_runs": runs,
        "worker_runs": runs,
        "agent_output": output[-_config_int(config, "activity", "global_output_limit", default=200):],
        "worker_output": output[-_config_int(config, "activity", "worker_output_limit", default=80):],
        "human_reports": _human_reports(events),
        "events": [_event_summary(event) for event in recent_events],
        "trace_files": _trace_files(events),
        "event_replay": "Event replay omitted from large dashboard state. Use /api/replay for full replay." if large_state else _compact_event_replay(recent_events),
        "trajectories": "Agent trajectories omitted from large dashboard state. Use /api/trajectories for full trajectories." if large_state else render_agent_trajectories(store),
        "worker_performance": worker_performance(agents, tasks, reports, artifacts),
        "all_tasks": [task.model_dump(mode="json") for task in all_tasks],
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


def _task_filter_scope(tasks: list[Any], task_id: str | None) -> set[str]:
    if not task_id:
        return set()
    if task_id not in {task.task_id for task in tasks}:
        return set()
    scope = {task_id}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task.task_id in scope:
                continue
            if task.parent_task_id in scope or task.root_goal_id == task_id:
                scope.add(task.task_id)
                changed = True
    return scope


def _event_matches_task_scope(event: Any, task_scope: set[str]) -> bool:
    if event.task_id in task_scope:
        return True
    for key in ("task_id", "parent_task_id", "root_goal_id", "root_task_id", "final_task_id", "reviewed_task_id"):
        value = event.payload.get(key)
        if isinstance(value, str) and value in task_scope:
            return True
    for key in ("task_ids", "current_task_ids"):
        value = event.payload.get(key)
        if isinstance(value, list) and task_scope.intersection(str(item) for item in value):
            return True
    return False


def _agent_ids_for_task_scope(
    agents: list[Any],
    tasks: list[Any],
    reports: list[Any],
    artifacts: list[Any],
    events: list[Any],
) -> set[str]:
    ids: set[str] = set()
    for task in tasks:
        for value in (task.assigned_to, task.assigned_by):
            if value:
                ids.add(str(value))
    for report in reports:
        ids.add(report.from_agent_id)
        ids.add(report.to_agent_id)
    for artifact in artifacts:
        ids.add(artifact.agent_id)
    for event in events:
        ids.add(event.actor_id)
        for key in ("agent_id", "from_agent_id", "to_agent_id", "target_agent_id", "assigned_to", "worker_id"):
            value = event.payload.get(key)
            if value:
                ids.add(str(value))
    by_id = {agent.id: agent for agent in agents}
    for agent_id in list(ids):
        current = by_id.get(agent_id)
        while current is not None and current.manager_id:
            ids.add(current.manager_id)
            current = by_id.get(current.manager_id)
    return {agent_id for agent_id in ids if agent_id in by_id}


def _agents_for_current_company(
    agents: list[Any],
    company: Company,
    *,
    required_agent_ids: set[str] | None = None,
) -> list[Any]:
    required_agent_ids = required_agent_ids or set()
    by_id = {agent.id: agent for agent in agents}
    matched_ids: set[str] = set()
    mission = (company.mission or "").strip()
    if mission:
        mission_marker = f"Company mission: {mission}"
        matched_ids = {
            agent.id
            for agent in agents
            if mission_marker in (agent.system_prompt or "")
        }

    if not matched_ids and required_agent_ids:
        matched_ids = set(required_agent_ids)
    if not matched_ids:
        return agents

    ids = set(matched_ids)
    ids.update(agent_id for agent_id in required_agent_ids if agent_id in by_id)
    for agent_id in list(ids):
        current = by_id.get(agent_id)
        while current is not None and current.manager_id:
            ids.add(current.manager_id)
            current = by_id.get(current.manager_id)
    return [agent for agent in agents if agent.id in ids]


def make_web_dashboard_server(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    db = Path(db_path)
    config_source_path = runtime_config_path(config_path)
    runtime_config = merge_runtime_config(config) if config is not None else load_runtime_config(config_source_path)
    dashboard_config = dashboard_config_from_runtime(runtime_config)
    demo_lock = threading.Lock()
    demo_status: dict[str, Any] = {
        "ok": True,
        "demo": "long-rfc",
        "running": False,
        "status": "idle",
        "run_id": "",
        "workspace": "",
        "started_at": "",
        "finished_at": "",
        "error": "",
        "result": {},
    }
    benchmark_lock = threading.Lock()
    benchmark_status: dict[str, Any] = {
        "ok": True,
        "demo": "real-llm-benchmark",
        "running": False,
        "status": "idle",
        "run_id": "",
        "workspace": "",
        "started_at": "",
        "finished_at": "",
        "error": "",
        "result": {},
    }
    designed_task_lock = threading.Lock()
    designed_task_status: dict[str, Any] = {
        "ok": True,
        "demo": "designed-task",
        "running": False,
        "status": "idle",
        "run_id": "",
        "workspace": "",
        "started_at": "",
        "finished_at": "",
        "error": "",
        "result": {},
        "root_task_id": "",
    }
    claude_steer_lock = threading.Lock()
    claude_steer_status: dict[str, Any] = {
        "ok": True,
        "demo": "claude-steer",
        "running": False,
        "status": "idle",
        "run_id": "",
        "workspace": "",
        "started_at": "",
        "finished_at": "",
        "error": "",
        "result": {},
        "task_id": "",
        "agent_id": "claude_worker",
    }
    simple_task_lock = threading.Lock()
    simple_task_status: dict[str, Any] = {
        "ok": True,
        "kind": "simple-task",
        "running": False,
        "status": "idle",
        "run_id": "",
        "workspace": "",
        "started_at": "",
        "finished_at": "",
        "error": "",
        "task_id": "",
        "agent_id": "claude_worker",
        "result": {},
    }
    mcp_oauth_lock = threading.Lock()
    pending_mcp_oauth: dict[str, dict[str, Any]] = {}

    def start_long_rfc_demo(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal demo_status
        demo_defaults = runtime_config.get("demos", {}).get("long_rfc", {})
        with demo_lock:
            if demo_status.get("running"):
                return HTTPStatus.CONFLICT, demo_status
            run_id = f"long-rfc-{int(time.time())}"
            workspace = Path(
                str(payload.get("workspace") or db.parent / "dashboard_demo_runs" / run_id)
            )
            url = str(payload.get("url") or demo_defaults.get("url") or DEFAULT_RFC_URL)
            delay_seconds = float(payload.get("delay_seconds", demo_defaults.get("delay_seconds", 0.8)))
            demo_status = {
                "ok": True,
                "demo": "long-rfc",
                "running": True,
                "status": "running",
                "run_id": run_id,
                "workspace": str(workspace),
                "started_at": _timestamp(),
                "finished_at": "",
                "error": "",
                "result": {},
            }
        thread = threading.Thread(
            target=run_long_rfc_demo_background,
            args=(run_id, workspace, url, delay_seconds),
            daemon=True,
        )
        thread.start()
        return HTTPStatus.ACCEPTED, demo_status

    def run_long_rfc_demo_background(run_id: str, workspace: Path, url: str, delay_seconds: float) -> None:
        nonlocal demo_status
        try:
            result = run_long_rfc_demo(db, workspace=workspace, url=url, delay_seconds=delay_seconds)
            with demo_lock:
                if demo_status.get("run_id") == run_id:
                    demo_status = {
                        **demo_status,
                        "running": False,
                        "status": "completed",
                        "finished_at": _timestamp(),
                        "result": result,
                    }
        except Exception as exc:  # noqa: BLE001 - dashboard should surface demo failures.
            error = str(exc)
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="demo_run_failed",
                    actor_id="system",
                    payload={"demo": "long-rfc", "run_id": run_id, "error": _single_line(error, 500)},
                )
            with demo_lock:
                if demo_status.get("run_id") == run_id:
                    demo_status = {
                        **demo_status,
                        "running": False,
                        "status": "failed",
                        "finished_at": _timestamp(),
                        "error": error,
                        "traceback": traceback.format_exc(),
                    }

    def start_real_llm_benchmark(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal benchmark_status
        benchmark_defaults = runtime_config.get("benchmarks", {})
        with benchmark_lock:
            if benchmark_status.get("running"):
                return HTTPStatus.CONFLICT, benchmark_status
            run_id = f"real-llm-benchmark-{int(time.time())}"
            workspace = Path(str(payload.get("workspace") or db.parent / "dashboard_benchmark_runs" / run_id))
            case_path = Path(str(payload.get("case_path") or benchmark_defaults.get("default_case_path") or DEFAULT_REAL_LLM_BENCHMARK_CASE))
            judge = str(payload.get("judge") or benchmark_defaults.get("judge") or "heuristic")
            use_llm = bool(payload.get("use_llm", benchmark_defaults.get("use_llm", True)))
            reset = bool(payload.get("reset", benchmark_defaults.get("reset", True)))
            benchmark_status = {
                "ok": True,
                "demo": "real-llm-benchmark",
                "running": True,
                "status": "running",
                "run_id": run_id,
                "workspace": str(workspace),
                "started_at": _timestamp(),
                "finished_at": "",
                "error": "",
                "result": {},
                "case_path": str(case_path),
                "judge": judge,
                "use_llm": use_llm,
                "reset": reset,
            }
        thread = threading.Thread(
            target=run_real_llm_benchmark_background,
            args=(run_id, workspace, case_path, use_llm, judge, reset),
            daemon=True,
        )
        thread.start()
        return HTTPStatus.ACCEPTED, benchmark_status

    def run_real_llm_benchmark_background(
        run_id: str,
        workspace: Path,
        case_path: Path,
        use_llm: bool,
        judge: str,
        reset: bool,
    ) -> None:
        nonlocal benchmark_status
        try:
            case = load_benchmark_case(case_path)
            result = run_benchmark_case(
                db,
                workspace=workspace,
                case=case,
                use_llm=use_llm,
                judge=judge if judge in {"none", "heuristic", "llm"} else "heuristic",  # type: ignore[arg-type]
                reset=reset,
                llm_json_config=runtime_config.get("benchmarks", {}).get("llm_json"),
                source_excerpt_chars=int(runtime_config.get("benchmarks", {}).get("source_excerpt_chars", 20000)),
            )
            with benchmark_lock:
                if benchmark_status.get("run_id") == run_id:
                    benchmark_status = {
                        **benchmark_status,
                        "running": False,
                        "status": "completed",
                        "finished_at": _timestamp(),
                        "result": result.model_dump(mode="json"),
                    }
        except Exception as exc:  # noqa: BLE001 - dashboard should surface benchmark failures.
            error = str(exc)
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="benchmark_run_failed",
                    actor_id="system",
                    payload={
                        "demo": "real-llm-benchmark",
                        "run_id": run_id,
                        "error": _single_line(error, 500),
                    },
                )
            with benchmark_lock:
                if benchmark_status.get("run_id") == run_id:
                    benchmark_status = {
                        **benchmark_status,
                        "running": False,
                        "status": "failed",
                        "finished_at": _timestamp(),
                        "error": error,
                        "traceback": traceback.format_exc(),
                    }

    def start_claude_steer_demo(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal claude_steer_status
        with claude_steer_lock:
            if claude_steer_status.get("running"):
                return HTTPStatus.CONFLICT, claude_steer_status
            run_id = f"claude-steer-{int(time.time())}"
            workspace = Path(str(payload.get("workspace") or db.parent / "dashboard_claude_steer_runs" / run_id))
            goal = str(
                payload.get("goal")
                or "Create a concise research note in STEERED_RESULT.md. Work for multiple visible steps, wait for possible human steering, then finish with evidence."
            )
            claude_steer_status = {
                "ok": True,
                "demo": "claude-steer",
                "running": True,
                "status": "running",
                "run_id": run_id,
                "workspace": str(workspace),
                "started_at": _timestamp(),
                "finished_at": "",
                "error": "",
                "result": {},
                "task_id": "",
                "agent_id": "claude_worker",
                "goal": goal,
            }
        thread = threading.Thread(target=run_claude_steer_demo_background, args=(run_id, workspace, goal), daemon=True)
        thread.start()
        return HTTPStatus.ACCEPTED, claude_steer_status

    def run_claude_steer_demo_background(run_id: str, workspace: Path, goal: str) -> None:
        nonlocal claude_steer_status
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init"], cwd=workspace, capture_output=True, text=True, check=False)
            readme = workspace / "README.md"
            if not readme.exists():
                readme.write_text("# Claude steer demo\n")
                subprocess.run(["git", "add", "README.md"], cwd=workspace, capture_output=True, text=True, check=False)
            with WorkforceRuntime(db) as runtime:
                runtime.initialize_org(Path("examples/simple_engineering_org/org.yaml"))
                task = runtime.create_task(
                    title="Claude steerable medium task",
                    objective=goal
                    + "\nBefore finalizing, inspect the workspace, write progress updates, and incorporate any human steering message if one arrives.",
                    assign_to="claude_worker",
                    acceptance_criteria=[
                        "STEERED_RESULT.md exists",
                        "Final report includes evidence and the done marker",
                        "Any human steering message is acknowledged or incorporated",
                    ],
                    required_artifacts=["steered_result"],
                )
                with claude_steer_lock:
                    if claude_steer_status.get("run_id") == run_id:
                        claude_steer_status = {**claude_steer_status, "task_id": task.task_id}
                worker = ClaudeCodeInteractiveWorker()
                run = worker.start_task(
                    task,
                    RuntimeContext(
                        runtime=runtime,
                        db_path=db,
                        workspace=workspace,
                        agent_id="claude_worker",
                        manager_id="engineering_manager",
                    ),
                )
            with claude_steer_lock:
                if claude_steer_status.get("run_id") == run_id:
                    claude_steer_status = {
                        **claude_steer_status,
                        "running": False,
                        "status": "completed" if run.returncode == 0 else "failed",
                        "finished_at": _timestamp(),
                        "result": {"task_id": task.task_id, "worker_run_id": run.run_id, "returncode": run.returncode},
                    }
        except Exception as exc:  # noqa: BLE001 - demo failure should be visible.
            error = str(exc)
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="claude_steer_demo_failed",
                    actor_id="system",
                    payload={"run_id": run_id, "error": _single_line(error, 500)},
                )
            with claude_steer_lock:
                if claude_steer_status.get("run_id") == run_id:
                    claude_steer_status = {
                        **claude_steer_status,
                        "running": False,
                        "status": "failed",
                        "finished_at": _timestamp(),
                        "error": error,
                        "traceback": traceback.format_exc(),
                    }

    def start_simple_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal simple_task_status
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "goal is required"}
        with simple_task_lock:
            if simple_task_status.get("running"):
                return HTTPStatus.CONFLICT, simple_task_status
            run_id = f"simple-task-{int(time.time())}"
            workspace = Path(str(payload.get("workspace") or db.parent / "dashboard_simple_task_runs" / run_id))
            simple_task_status = {
                "ok": True,
                "kind": "simple-task",
                "running": True,
                "status": "running",
                "run_id": run_id,
                "workspace": str(workspace),
                "started_at": _timestamp(),
                "finished_at": "",
                "error": "",
                "task_id": "",
                "agent_id": "claude_worker",
                "goal": goal,
                "result": {},
            }
        thread = threading.Thread(target=run_simple_task_background, args=(run_id, workspace, goal), daemon=True)
        thread.start()
        return HTTPStatus.ACCEPTED, simple_task_status

    def run_simple_task_background(run_id: str, workspace: Path, goal: str) -> None:
        nonlocal simple_task_status
        task = None
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init"], cwd=workspace, capture_output=True, text=True, check=False)
            readme = workspace / "README.md"
            if not readme.exists():
                readme.write_text("# Workforce Runtime simple task\n")
                subprocess.run(["git", "add", "README.md"], cwd=workspace, capture_output=True, text=True, check=False)
            with WorkforceRuntime(db) as runtime:
                runtime.initialize_org(Path("examples/simple_engineering_org/org.yaml"))
                task = runtime.create_task(
                    title=_single_line(goal, 72) or "Simple dashboard task",
                    objective=(
                        f"{goal}\n\n"
                        "Use the current workspace as needed. Create TASK_RESULT.md as the concise human-facing final report. "
                        "Include sections: Result, Evidence, Risks, Next action. "
                        "When complete, provide a concise final report and include WORKFORCE_TASK_DONE on its own line."
                    ),
                    assign_to="claude_worker",
                    acceptance_criteria=[
                        "TASK_RESULT.md exists",
                        "Final report is concise and human-facing",
                        "Evidence and next action are stated",
                    ],
                    required_artifacts=["task_result"],
                )
                with simple_task_lock:
                    if simple_task_status.get("run_id") == run_id:
                        simple_task_status = {**simple_task_status, "task_id": task.task_id}
                worker = ClaudeCodeInteractiveWorker()
                run = worker.start_task(
                    task,
                    RuntimeContext(
                        runtime=runtime,
                        db_path=db,
                        workspace=workspace,
                        agent_id="claude_worker",
                        manager_id="engineering_manager",
                    ),
                )
                reports = runtime.store.list_reports_by_task(task.task_id)
            result_path = workspace / "TASK_RESULT.md"
            report_text = result_path.read_text() if result_path.exists() else ""
            if not report_text and reports:
                report_text = reports[-1].summary
            if task is not None:
                with WorkforceRuntime(db) as report_runtime:
                    if result_path.exists():
                        report_runtime.register_artifact(
                            Artifact(
                                artifact_id=f"artifact_{uuid4().hex[:12]}",
                                task_id=task.task_id,
                                agent_id="claude_worker",
                                type="task_result",
                                path=str(result_path),
                                description="Human-facing result from the simple dashboard task.",
                            )
                        )
                    report_runtime.report_to_human(
                        from_agent_id="ceo",
                        task_id=task.task_id,
                        title=f"Task completed: {_single_line(goal, 72)}",
                        message=report_text or "The simple dashboard task finished without a separate result artifact.",
                        status="completed" if run.returncode == 0 else "failed",
                        confidence=0.8 if run.returncode == 0 else 0.35,
                        next_action="Review the task result and trace.",
                    )
            with simple_task_lock:
                if simple_task_status.get("run_id") == run_id:
                    simple_task_status = {
                        **simple_task_status,
                        "running": False,
                        "status": "completed" if run.returncode == 0 else "failed",
                        "finished_at": _timestamp(),
                        "result": {
                            "task_id": task.task_id,
                            "worker_run_id": run.run_id,
                            "returncode": run.returncode,
                            "report_text": report_text,
                            "result_path": str(result_path) if result_path.exists() else "",
                            "report_id": reports[-1].report_id if reports else "",
                        },
                    }
        except Exception as exc:  # noqa: BLE001 - dashboard should surface task failures.
            error = str(exc)
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="simple_task_run_failed",
                    actor_id="system",
                    task_id=task.task_id if task else None,
                    payload={"run_id": run_id, "error": _single_line(error, 500)},
                )
            with simple_task_lock:
                if simple_task_status.get("run_id") == run_id:
                    simple_task_status = {
                        **simple_task_status,
                        "running": False,
                        "status": "failed",
                        "finished_at": _timestamp(),
                        "error": error,
                        "traceback": traceback.format_exc(),
                    }

    def design_task_config(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        designed_defaults = runtime_config.get("designed_task", {})
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "goal is required"}
        headcount_limit = _payload_int(payload, "headcount_limit", int(designed_defaults.get("headcount_limit") or 6))
        token_budget = _payload_int(payload, "token_budget", int(designed_defaults.get("token_budget") or 600000))
        management_model = str(payload.get("management_model") or designed_defaults.get("management_model") or "openai/gpt-oss-120b:free")
        worker_model = str(payload.get("worker_model") or designed_defaults.get("worker_model") or "poolside/laguna-m.1:free")
        decision_backend = str(payload.get("decision_backend") or designed_defaults.get("decision_backend") or "codex")
        management_worker_type = str(payload.get("management_worker_type") or designed_defaults.get("management_worker_type") or "codex")
        worker_worker_type = str(payload.get("worker_worker_type") or designed_defaults.get("worker_worker_type") or "codex")
        use_llm = bool(payload.get("use_llm", True))
        try:
            request = OrgDesignRequest(
                goal=goal,
                company_name=str(payload.get("company_name") or designed_defaults.get("company_name") or "Designed Task Workforce"),
                headcount_limit=headcount_limit,
                token_budget=token_budget,
                management_model=management_model,
                worker_model=worker_model,
                decision_backend=decision_backend,
                management_worker_type=management_worker_type,
                worker_worker_type=worker_worker_type,
            )
            organization = OrgDesigner().design(request, use_llm=use_llm, allow_fallback=True)
        except Exception as exc:  # noqa: BLE001 - return validation/design errors to the dashboard.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid design request: {exc}"}
        title = _single_line(goal, 72) or "Designed Task"
        case = BenchmarkCase(
            id=f"designed_task_{int(time.time())}",
            title=title,
            goal=goal,
            constraints=[str(item) for item in payload.get("constraints") or designed_defaults.get("constraints") or ["Preserve the user's stated objective."]],
            acceptance_criteria=[
                str(item)
                for item in payload.get("acceptance_criteria")
                or designed_defaults.get("acceptance_criteria")
                or ["Produce a concise result artifact.", "Report evidence, risks, and next action."]
            ],
            expected_artifacts=[str(item) for item in payload.get("expected_artifacts") or designed_defaults.get("expected_artifacts") or ["task_result"]],
            headcount_limit=headcount_limit,
            token_budget=token_budget,
            management_model=management_model,
            worker_model=worker_model,
            judge_model=str(payload.get("judge_model") or designed_defaults.get("judge_model") or management_model),
        )
        config_payload = {
            "case": case.model_dump(mode="json"),
            "organization": organization.model_dump(mode="json"),
            "run": {
                "use_llm": use_llm,
                "decision_backend": decision_backend,
                "judge": str(payload.get("judge") or designed_defaults.get("judge") or "heuristic"),
                "reset": bool(payload.get("reset", designed_defaults.get("reset", False))),
            },
        }
        return HTTPStatus.OK, {"ok": True, "config": config_payload}

    def start_designed_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal designed_task_status
        config_payload = payload.get("config")
        if not isinstance(config_payload, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "config object is required"}
        try:
            case = BenchmarkCase.model_validate(config_payload.get("case"))
            organization = Organization.model_validate(config_payload.get("organization"))
        except Exception as exc:  # noqa: BLE001 - API should return validation errors.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid config: {exc}"}
        run_config = config_payload.get("run") if isinstance(config_payload.get("run"), dict) else {}
        with designed_task_lock:
            if designed_task_status.get("running"):
                return HTTPStatus.CONFLICT, designed_task_status
            run_id = f"designed-task-{int(time.time())}"
            workspace = Path(str(payload.get("workspace") or db.parent / "dashboard_task_runs" / run_id))
            designed_task_status = {
                "ok": True,
                "demo": "designed-task",
                "running": True,
                "status": "running",
                "run_id": run_id,
                "workspace": str(workspace),
                "started_at": _timestamp(),
                "finished_at": "",
                "error": "",
                "result": {},
                "root_task_id": "",
                "case_id": case.id,
            }
        thread = threading.Thread(
            target=run_designed_task_background,
            args=(
                run_id,
                workspace,
                case,
                organization,
                bool(run_config.get("use_llm", True)),
                str(run_config.get("judge") or "heuristic"),
                bool(run_config.get("reset", False)),
            ),
            daemon=True,
        )
        thread.start()
        return HTTPStatus.ACCEPTED, designed_task_status

    def update_runtime_config(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal runtime_config, dashboard_config
        raw_config = payload.get("config", payload)
        if not isinstance(raw_config, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "config object is required"}
        try:
            next_config = merge_runtime_config(raw_config)
            path = save_runtime_config(next_config, config_source_path)
        except Exception as exc:  # noqa: BLE001 - return validation/write errors to the dashboard.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        runtime_config = next_config
        dashboard_config = dashboard_config_from_runtime(runtime_config)
        with WorkforceRuntime(db) as runtime:
            runtime.record_event(
                event_type="runtime_config_updated",
                actor_id="system",
                payload={"path": str(path)},
            )
        return HTTPStatus.OK, {"ok": True, "path": str(path), "config": runtime_config}

    def mcp_settings_payload() -> dict[str, Any]:
        external = runtime_config.get("external_mcp") if isinstance(runtime_config.get("external_mcp"), dict) else {}
        return {
            "ok": True,
            "path": str(config_source_path),
            "external_mcp": copy.deepcopy(external),
            "servers": copy.deepcopy(external.get("servers") if isinstance(external.get("servers"), list) else []),
        }

    def save_mcp_server_from_dashboard(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal runtime_config, dashboard_config
        server_payload = payload.get("server", payload)
        if not isinstance(server_payload, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server object is required"}
        server_id = str(server_payload.get("id") or "").strip()
        url = str(server_payload.get("url") or "").strip()
        if not server_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server id is required"}
        if not url:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server url is required"}
        auth_payload = server_payload.get("auth") if isinstance(server_payload.get("auth"), dict) else {}
        auth_type = str(auth_payload.get("type") or server_payload.get("auth_type") or "none").strip() or "none"
        auth: dict[str, Any] = {"type": auth_type}
        if auth_type == "bearer":
            auth["token_env"] = str(auth_payload.get("token_env") or server_payload.get("token_env") or "").strip()
        elif auth_type == "oauth":
            auth["metadata"] = copy.deepcopy(auth_payload.get("metadata") if isinstance(auth_payload.get("metadata"), dict) else {})
        server_entry: dict[str, Any] = {
            "id": server_id,
            "enabled": bool(server_payload.get("enabled", True)),
            "transport": str(server_payload.get("transport") or "http"),
            "url": url,
            "tool_prefix": str(server_payload.get("tool_prefix") or server_id).strip() or server_id,
            "auth": auth,
            "allowed_agent_ids": _string_list(server_payload.get("allowed_agent_ids"), default=["*"]),
            "allowed_roles": _string_list(server_payload.get("allowed_roles")),
            "allowed_departments": _string_list(server_payload.get("allowed_departments")),
            "allowed_worker_types": _string_list(server_payload.get("allowed_worker_types")),
            "allowed_tools": _string_list(server_payload.get("allowed_tools"), default=["*"]),
            "timeout_seconds": _optional_positive_int(server_payload.get("timeout_seconds"), default=30),
            "queue": {"enabled": bool(server_payload.get("queue_enabled", server_payload.get("queue", {}).get("enabled", True) if isinstance(server_payload.get("queue"), dict) else True))},
            "tools": copy.deepcopy(server_payload.get("tools") if isinstance(server_payload.get("tools"), list) else []),
        }
        next_config = merge_runtime_config(runtime_config)
        external = copy.deepcopy(next_config.get("external_mcp") if isinstance(next_config.get("external_mcp"), dict) else {})
        servers = external.get("servers")
        if not isinstance(servers, list):
            servers = []
        replaced = False
        next_servers: list[dict[str, Any]] = []
        for item in servers:
            if isinstance(item, dict) and str(item.get("id") or "") == server_id:
                next_servers.append(server_entry)
                replaced = True
            else:
                next_servers.append(copy.deepcopy(item))
        if not replaced:
            next_servers.append(server_entry)
        external["servers"] = next_servers
        next_config["external_mcp"] = external
        try:
            path = save_runtime_config(next_config, config_source_path)
        except Exception as exc:  # noqa: BLE001 - return validation/write errors to the dashboard.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        runtime_config = merge_runtime_config(next_config)
        dashboard_config = dashboard_config_from_runtime(runtime_config)
        with WorkforceRuntime(db) as runtime:
            runtime.record_event(
                event_type="external_mcp_server_saved",
                actor_id="human",
                payload={"server_id": server_id, "url": url, "path": str(path)},
            )
        return HTTPStatus.OK, {**mcp_settings_payload(), "saved": server_entry}

    def delete_mcp_server_from_dashboard(server_id: str) -> tuple[int, dict[str, Any]]:
        nonlocal runtime_config, dashboard_config
        server_id = server_id.strip()
        if not server_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server id is required"}
        next_config = merge_runtime_config(runtime_config)
        external = copy.deepcopy(next_config.get("external_mcp") if isinstance(next_config.get("external_mcp"), dict) else {})
        servers = external.get("servers")
        if not isinstance(servers, list):
            servers = []
        next_servers: list[dict[str, Any]] = []
        removed: dict[str, Any] | None = None
        for item in servers:
            if isinstance(item, dict) and str(item.get("id") or "") == server_id:
                removed = copy.deepcopy(item)
                continue
            next_servers.append(copy.deepcopy(item) if isinstance(item, dict) else item)
        if removed is None:
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": f"MCP server not found: {server_id}"}
        external["servers"] = next_servers
        next_config["external_mcp"] = external
        try:
            path = save_runtime_config(next_config, config_source_path)
        except Exception as exc:  # noqa: BLE001 - return validation/write errors to the dashboard.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        runtime_config = merge_runtime_config(next_config)
        dashboard_config = dashboard_config_from_runtime(runtime_config)
        with WorkforceRuntime(db) as runtime:
            runtime.record_event(
                event_type="external_mcp_server_deleted",
                actor_id="human",
                payload={"server_id": server_id, "url": str(removed.get("url") or ""), "path": str(path)},
            )
        return HTTPStatus.OK, {**mcp_settings_payload(), "deleted": server_id}

    def start_mcp_oauth_from_dashboard(payload: dict[str, Any], dashboard_callback_url: str) -> tuple[int, dict[str, Any]]:
        server_payload = payload.get("server", payload)
        if not isinstance(server_payload, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server object is required"}
        server_id = str(server_payload.get("id") or server_payload.get("server_id") or "").strip()
        url = str(server_payload.get("url") or "").strip()
        if not server_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server id is required"}
        if not url:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "server url is required"}
        external = runtime_config.get("external_mcp") if isinstance(runtime_config.get("external_mcp"), dict) else {}
        oauth_defaults = external.get("oauth") if isinstance(external.get("oauth"), dict) else {}
        timeout = _optional_positive_int(
            server_payload.get("timeout_seconds") or oauth_defaults.get("timeout_seconds"),
            default=300,
        )
        callback_url = str(server_payload.get("callback_url") or oauth_defaults.get("callback_url") or dashboard_callback_url)
        scopes = _string_list(server_payload.get("scope") or server_payload.get("scopes"))
        client_id = str(server_payload.get("client_id") or "").strip()
        client_secret = str(server_payload.get("client_secret") or "").strip()
        resource = str(server_payload.get("resource") or "").strip()
        try:
            probe = probe_mcp_auth(url, timeout_seconds=min(float(timeout), 10.0))
            handle = start_oauth_login_for_callback(
                server_id=server_id,
                url=url,
                callback_url=callback_url,
                metadata=probe.oauth_metadata if probe.auth_status == "oauth" else None,
                scopes=scopes,
                client_id=client_id,
                client_secret=client_secret,
                resource=resource,
                timeout_seconds=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - surface OAuth discovery/start errors to dashboard.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        with mcp_oauth_lock:
            _prune_pending_mcp_oauth(pending_mcp_oauth)
            pending_mcp_oauth[handle.state] = {
                "server_id": server_id,
                "url": url,
                "authorization_url": handle.authorization_url,
                "created_at": time.time(),
                "handle": handle,
            }
        with WorkforceRuntime(db) as runtime:
            runtime.record_event(
                event_type="external_mcp_oauth_started",
                actor_id="human",
                payload={"server_id": server_id, "url": url, "redirect_uri": handle.redirect_uri},
            )
        return HTTPStatus.ACCEPTED, {
            "ok": True,
            "server_id": server_id,
            "url": url,
            "authorization_url": handle.authorization_url,
            "redirect_uri": handle.redirect_uri,
            "message": "Open the authorization URL and complete the OAuth callback.",
        }

    def complete_mcp_oauth_from_dashboard(path: str, query: str) -> tuple[int, str]:
        callback_id = path.removeprefix("/api/settings/mcp/oauth/callback/").strip("/")
        params = parse_qs(query)
        code = _first_query_param(params, "code")
        state = _first_query_param(params, "state")
        error = _first_query_param(params, "error")
        error_description = _first_query_param(params, "error_description")
        if not state:
            return HTTPStatus.BAD_REQUEST, _oauth_callback_html("OAuth login failed", "The callback was missing state.", False)
        with mcp_oauth_lock:
            pending = pending_mcp_oauth.pop(state, None)
        if not isinstance(pending, dict):
            return HTTPStatus.BAD_REQUEST, _oauth_callback_html(
                "OAuth login expired",
                "Workforce Runtime could not find this OAuth login. Start OAuth again from the dashboard.",
                False,
            )
        server_id = str(pending.get("server_id") or "")
        url = str(pending.get("url") or "")
        authorization_url = str(pending.get("authorization_url") or "")
        handle = pending.get("handle")
        try:
            if error:
                raise RuntimeError(error_description or error)
            if callback_id != getattr(handle, "callback_id", ""):
                raise RuntimeError("OAuth callback path did not match the pending login")
            result = handle.complete(code=code, state=state)
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="external_mcp_oauth_finished",
                    actor_id="human",
                    payload={
                        "server_id": result.server_id,
                        "url": result.url,
                        "token_path": str(result.token_path),
                        "scopes": list(result.scopes),
                        "expires_at": result.expires_at,
                    },
                )
            return HTTPStatus.OK, _oauth_callback_html(
                "Authentication complete",
                "Workforce Runtime saved the MCP OAuth token. You can close this tab.",
                True,
            )
        except Exception as exc:  # noqa: BLE001 - record callback/token exchange failures.
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="external_mcp_oauth_failed",
                    actor_id="human",
                    payload={
                        "server_id": server_id,
                        "url": url,
                        "authorization_url": authorization_url,
                        "error": str(exc),
                    },
                )
            return HTTPStatus.BAD_REQUEST, _oauth_callback_html("OAuth login failed", str(exc), False)

    def wait_mcp_oauth_background(server_id: str, url: str, authorization_url: str, handle: Any) -> None:
        try:
            result = handle.wait()
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="external_mcp_oauth_finished",
                    actor_id="human",
                    payload={
                        "server_id": result.server_id,
                        "url": result.url,
                        "token_path": str(result.token_path),
                        "scopes": list(result.scopes),
                        "expires_at": result.expires_at,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - record async auth failures.
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="external_mcp_oauth_failed",
                    actor_id="human",
                    payload={
                        "server_id": server_id,
                        "url": url,
                        "authorization_url": authorization_url,
                        "error": str(exc),
                    },
                )

    def skill_settings_payload() -> dict[str, Any]:
        with WorkforceRuntime(db) as runtime:
            skills = runtime.list_skills()
            assignments = runtime.list_skill_assignments()
            agents_payload = [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "role": agent.role,
                    "department": agent.department,
                    "worker_type": agent.worker_type,
                }
                for agent in runtime.store.list_agents()
            ]
            materializations = runtime.list_skill_materializations()
        return {
            "ok": True,
            "config": copy.deepcopy(runtime_config.get("skills") if isinstance(runtime_config.get("skills"), dict) else {}),
            "skills": [skill.model_dump(mode="json") for skill in skills],
            "assignments": [assignment.model_dump(mode="json") for assignment in assignments],
            "agents": agents_payload,
            "materializations": [item.model_dump(mode="json") for item in materializations],
        }

    def create_skill_from_dashboard(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        instructions = str(payload.get("instructions") or "").strip()
        if not name:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "skill name is required"}
        if not description:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "skill description is required"}
        provider_targets = _string_list(payload.get("provider_targets"), default=["codex", "claude_code"])
        status = str(payload.get("status") or "approved").strip() or "approved"
        try:
            with WorkforceRuntime(db) as runtime:
                skill = runtime.create_skill(
                    name=name,
                    description=description,
                    instructions=instructions,
                    status=status,
                    provider_targets=provider_targets,
                    source=str(payload.get("source") or "dashboard"),
                    actor_id=str(payload.get("actor_id") or "human"),
                )
        except Exception as exc:  # noqa: BLE001 - return model/store validation errors.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        return HTTPStatus.OK, {**skill_settings_payload(), "created": skill.model_dump(mode="json")}

    def assign_skill_from_dashboard(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        skill_id = str(payload.get("skill_id") or "").strip()
        target_type = str(payload.get("target_type") or "").strip()
        target_id = str(payload.get("target_id") or "*").strip() or "*"
        if not skill_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "skill_id is required"}
        if target_type not in {"global", "agent", "role", "department", "worker_type"}:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "target_type is invalid"}
        try:
            with WorkforceRuntime(db) as runtime:
                assignment = runtime.assign_skill(
                    skill_id=skill_id,
                    target_type=target_type,  # type: ignore[arg-type]
                    target_id=target_id,
                    actor_id=str(payload.get("actor_id") or "human"),
                    enabled=bool(payload.get("enabled", True)),
                    materialize_on_start=bool(payload.get("materialize_on_start", True)),
                )
        except Exception as exc:  # noqa: BLE001 - return model/store validation errors.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        return HTTPStatus.OK, {**skill_settings_payload(), "created_assignment": assignment.model_dump(mode="json")}

    def export_task_trace_from_dashboard(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "task_id is required"}
        runtime_defaults = runtime_config.get("runtime", {})
        workspace = payload.get("workspace") or runtime_defaults.get("task_trace_dir") or None
        include_file_contents = bool(
            payload.get("include_file_contents", runtime_defaults.get("task_trace_include_file_contents", True))
        )
        max_file_bytes = _payload_int(
            payload,
            "max_file_bytes",
            int(runtime_defaults.get("task_trace_max_file_bytes") or 500000),
        )
        try:
            with WorkforceRuntime(db) as runtime:
                trace = runtime.export_task_trace(
                    task_id,
                    workspace=workspace,
                    trace_id=str(payload.get("trace_id") or "") or None,
                    include_descendants=bool(payload.get("include_descendants", True)),
                    include_file_contents=include_file_contents,
                    max_file_bytes=max_file_bytes,
                )
        except Exception as exc:  # noqa: BLE001 - API should return task/trace errors.
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        return HTTPStatus.OK, {
            "ok": True,
            "trace": trace.model_dump(mode="json"),
            "path": trace.path,
            "url": f"/api/file?path={quote(trace.path)}",
        }

    def rename_task_from_dashboard(task_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        task_id = (task_id or "").strip()
        if not task_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "task_id is required"}
        title = str(payload.get("title") or "").strip()
        if not title:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "title is required"}
        with WorkforceRuntime(db) as runtime:
            task = runtime.store.get_task(task_id)
            if task is None:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": f"task not found: {task_id}"}
            previous_title = task.title
            if title == previous_title:
                return HTTPStatus.OK, {"ok": True, "task_id": task_id, "title": title}
            updated = task.model_copy(update={"title": title})
            runtime.store.save_task(updated)
            runtime.record_event(
                event_type="task_renamed",
                actor_id="human",
                payload={"task_id": task_id, "title": title, "previous_title": previous_title},
            )
        return HTTPStatus.OK, {"ok": True, "task_id": task_id, "title": title}

    def delete_task_from_dashboard(task_id: str) -> tuple[int, dict[str, Any]]:
        task_id = (task_id or "").strip()
        if not task_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "task_id is required"}
        with WorkforceRuntime(db) as runtime:
            task = runtime.store.get_task(task_id)
            if task is None:
                return HTTPStatus.NOT_FOUND, {"ok": False, "error": f"task not found: {task_id}"}
            title = task.title
            runtime.store.delete_task(task_id)
            runtime.record_event(
                event_type="task_deleted",
                actor_id="human",
                payload={"task_id": task_id, "title": title},
            )
        return HTTPStatus.OK, {"ok": True, "deleted": task_id}

    def list_clarifications_payload(only_pending: bool = False) -> tuple[int, dict[str, Any]]:
        with WorkforceRuntime(db) as runtime:
            items = runtime.list_clarifications()
        clarifications = [item.model_dump(mode="json") for item in items]
        pending = [c for c in clarifications if c.get("status") == "awaiting_human"]
        return HTTPStatus.OK, {
            "ok": True,
            "clarifications": pending if only_pending else clarifications,
            "pending_human_count": len(pending),
        }

    def answer_clarification_from_dashboard(clarification_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        clarification_id = (clarification_id or "").strip()
        answer = str(payload.get("answer") or "").strip()
        if not clarification_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "clarification_id is required"}
        if not answer:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "answer is required"}
        try:
            with WorkforceRuntime(db) as runtime:
                clarification = runtime.answer_clarification(
                    clarification_id=clarification_id,
                    from_agent_id="human",
                    answer=answer,
                )
        except KeyError as exc:
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)}
        except (ValueError, PermissionError) as exc:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
        return HTTPStatus.OK, {"ok": True, "clarification": clarification.model_dump(mode="json")}

    def steer_agent_from_dashboard(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        agent_id = str(payload.get("agent_id") or "").strip()
        message = str(payload.get("message") or "").strip()
        task_id = str(payload.get("task_id") or "").strip() or None
        action = str(payload.get("action") or "message").strip()
        from_agent_id = str(payload.get("from_agent_id") or "human").strip() or "human"
        if not agent_id:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "agent_id is required"}
        if action != "interrupt" and not message:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "message is required"}
        with WorkforceRuntime(db) as runtime:
            runtime.record_event(
                event_type="human_agent_steer_requested",
                actor_id=from_agent_id,
                task_id=task_id,
                payload={"target_agent_id": agent_id, "message": message, "action": action},
            )
        result = (
            STEERABLE_SESSIONS.interrupt(agent_id=agent_id, task_id=task_id, from_agent_id=from_agent_id)
            if action == "interrupt"
            else STEERABLE_SESSIONS.steer(agent_id=agent_id, task_id=task_id, from_agent_id=from_agent_id, message=message)
        )
        if not result.ok:
            with WorkforceRuntime(db) as runtime:
                if action != "interrupt":
                    session = latest_provider_session(runtime, agent_id=agent_id, task_id=task_id)
                    if session is not None:
                        thread = threading.Thread(
                            target=resume_provider_session_background,
                            args=(agent_id, task_id, message, from_agent_id),
                            daemon=True,
                        )
                        thread.start()
                        return HTTPStatus.ACCEPTED, {
                            "ok": True,
                            "status": "resume_started",
                            "agent_id": agent_id,
                            "task_id": task_id or session.task_id,
                            "provider_session_id": session.provider_session_id,
                            "message": "No live session; started provider session resume.",
                        }
                    active_run = _active_worker_run_for_agent(runtime.store.list_events(), agent_id=agent_id, task_id=task_id)
                    if active_run is not None:
                        effective_task_id = str(active_run.get("task_id") or task_id or "")
                        queued_event_id = queue_steer_for_resume(
                            runtime,
                            agent_id=agent_id,
                            task_id=effective_task_id,
                            message=message,
                            from_agent_id=from_agent_id,
                        )
                        return HTTPStatus.ACCEPTED, {
                            "ok": True,
                            "status": "queued_for_resume",
                            "agent_id": agent_id,
                            "task_id": effective_task_id,
                            "queued_event_id": queued_event_id,
                            "message": "No live session yet; steering was queued for the next provider-session turn.",
                        }
                runtime.record_event(
                    event_type="human_agent_steer_failed",
                    actor_id=from_agent_id,
                    task_id=task_id,
                    payload={"target_agent_id": agent_id, "message": message, "action": action, "status": result.status},
                )
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "status": result.status,
                "agent_id": agent_id,
                "task_id": task_id or "",
                "message": "No active steerable session for that agent/task.",
            }
        return HTTPStatus.ACCEPTED, {
            "ok": True,
            "status": result.status,
            "run_id": result.run_id,
            "agent_id": result.agent_id,
            "task_id": result.task_id,
        }

    def resume_provider_session_background(agent_id: str, task_id: str | None, message: str, from_agent_id: str) -> None:
        try:
            with WorkforceRuntime(db) as runtime:
                result = resume_provider_session(
                    runtime,
                    agent_id=agent_id,
                    task_id=task_id,
                    message=message,
                    from_agent_id=from_agent_id,
                )
                if not result.ok:
                    runtime.record_event(
                        event_type="human_agent_steer_failed",
                        actor_id=from_agent_id,
                        task_id=task_id,
                        payload={
                            "target_agent_id": agent_id,
                            "message": message,
                            "action": "resume",
                            "status": result.status,
                        },
                    )
        except Exception as exc:  # noqa: BLE001 - background failures should be visible in the event log.
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="human_agent_steer_failed",
                    actor_id=from_agent_id,
                    task_id=task_id,
                    payload={
                        "target_agent_id": agent_id,
                        "message": message,
                        "action": "resume",
                        "status": "exception",
                        "error": str(exc),
                    },
                )

    def run_designed_task_background(
        run_id: str,
        workspace: Path,
        case: BenchmarkCase,
        organization: Organization,
        use_llm: bool,
        judge: str,
        reset: bool,
    ) -> None:
        nonlocal designed_task_status
        def mark_root_task_created(root_task_id: str) -> None:
            nonlocal designed_task_status
            with designed_task_lock:
                if designed_task_status.get("run_id") == run_id:
                    designed_task_status = {
                        **designed_task_status,
                        "root_task_id": root_task_id,
                        "status": "running",
                    }

        try:
            if reset and db.exists():
                db.unlink()
            if reset and workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir(parents=True, exist_ok=True)
            with WorkforceRuntime(db) as runtime:
                runtime.initialize_organization(organization, source=f"org_designer:{case.id}")
                runtime.record_event(
                    event_type="designed_task_run_started",
                    actor_id="system",
                    payload={
                        "run_id": run_id,
                        "case_id": case.id,
                        "title": case.title,
                        "decision_backend": runtime_config.get("designed_task", {}).get("decision_backend", "codex"),
                        "judge": judge,
                        "use_llm": use_llm,
                    },
                )
                root_agent = next((agent for agent in organization.agents if agent.manager_id is None), organization.agents[0])
                root_task = runtime.create_task(
                    title=case.title,
                    objective=case.goal,
                    assign_to=root_agent.id,
                    assigned_by="human",
                    constraints=case.constraints,
                    acceptance_criteria=case.acceptance_criteria,
                    required_artifacts=case.expected_artifacts,
                )
                mark_root_task_created(root_task.task_id)
                queue_config = runtime_config.get("queue", {})
                max_cycles = int(queue_config.get("max_dispatch_cycles") or max(25, len(organization.agents) * 10))
                dispatch_result = AgentInboxDispatcher(
                    runtime,
                    db_path=db,
                    workspace=workspace,
                ).run_until_idle(max_cycles=max_cycles)
                root_task_after = runtime.require_task(root_task.task_id)
                trace = runtime.export_task_trace(
                    root_task.task_id,
                    workspace=workspace / "task_traces",
                    include_descendants=True,
                    include_file_contents=bool(runtime_config.get("runtime", {}).get("task_trace_include_file_contents", True)),
                    max_file_bytes=int(runtime_config.get("runtime", {}).get("task_trace_max_file_bytes", 500000)),
                )
                result_payload = {
                    "root_task_id": root_task.task_id,
                    "root_task_status": root_task_after.status,
                    "dispatch": {
                        "claimed": dispatch_result.claimed,
                        "completed": dispatch_result.completed,
                        "failed": dispatch_result.failed,
                        "max_cycles": max_cycles,
                    },
                    "agent_count": len(organization.agents),
                    "task_count": len(runtime.store.list_tasks()),
                    "report_count": len(runtime.store.list_reports()),
                    "trace_id": trace.trace_id,
                    "trace_path": trace.path,
                }
            with designed_task_lock:
                if designed_task_status.get("run_id") == run_id:
                    designed_task_status = {
                        **designed_task_status,
                        "running": False,
                        "status": "completed",
                        "finished_at": _timestamp(),
                        "root_task_id": result_payload["root_task_id"],
                        "result": result_payload,
                    }
        except Exception as exc:  # noqa: BLE001 - dashboard should surface task failures.
            error = str(exc)
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="designed_task_run_failed",
                    actor_id="system",
                    payload={"run_id": run_id, "case_id": case.id, "error": _single_line(error, 500)},
                )
            with designed_task_lock:
                if designed_task_status.get("run_id") == run_id:
                    designed_task_status = {
                        **designed_task_status,
                        "running": False,
                        "status": "failed",
                        "finished_at": _timestamp(),
                        "error": error,
                        "traceback": traceback.format_exc(),
                    }

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
            if parsed.path == "/assets/elk.bundled.js":
                self._send_file(ELK_JS_PATH, content_type="application/javascript; charset=utf-8")
                return
            if parsed.path.startswith("/assets/"):
                asset_path = _dashboard_static_asset(parsed.path)
                if asset_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
                    return
                self._send_file(asset_path, content_type=_asset_content_type(asset_path), cache_control="no-store")
                return
            if parsed.path == "/api/state":
                query = parse_qs(parsed.query)
                task_id = str(query.get("task_id", [""])[0] or "")
                with WorkforceRuntime(db) as runtime:
                    state = build_web_dashboard_state(runtime.store, dashboard_config, task_id_filter=task_id or None)
                self._send_json(state)
                return
            if parsed.path == "/api/agent":
                query = parse_qs(parsed.query)
                agent_id = str(query.get("agent_id", [""])[0] or "")
                if not agent_id:
                    self._send_json({"ok": False, "error": "agent_id is required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                with WorkforceRuntime(db) as runtime:
                    agent = runtime.get_agent(agent_id)
                    if agent is None:
                        self._send_json({"ok": False, "error": f"agent not found: {agent_id}"}, status=HTTPStatus.NOT_FOUND)
                        return
                    profile = runtime.store.get_agent_personal_profile(agent_id)
                self._send_json(
                    {
                        "ok": True,
                        "agent": {
                            **agent.model_dump(mode="json"),
                            "personal_profile": profile.model_dump(mode="json") if profile else {},
                            "model_capabilities": model_capabilities(agent.model, {"models": dashboard_config.get("models", {})}) or {},
                        },
                    }
                )
                return
            if parsed.path == "/api/config":
                self._send_json(dashboard_config)
                return
            if parsed.path == "/api/runtime-config":
                self._send_json({"ok": True, "path": str(config_source_path), "config": runtime_config})
                return
            if parsed.path == "/api/settings/mcp":
                self._send_json(mcp_settings_payload())
                return
            if parsed.path.startswith("/api/settings/mcp/oauth/callback/"):
                status, html = complete_mcp_oauth_from_dashboard(parsed.path, parsed.query)
                self._send_html(html, status=status)
                return
            if parsed.path == "/api/settings/skills":
                self._send_json(skill_settings_payload())
                return
            if parsed.path == "/api/demos/long-rfc/status":
                with demo_lock:
                    self._send_json(dict(demo_status))
                return
            if parsed.path == "/api/demos/real-llm-benchmark/status":
                with benchmark_lock:
                    self._send_json(dict(benchmark_status))
                return
            if parsed.path == "/api/designed-task/status":
                with designed_task_lock:
                    self._send_json(dict(designed_task_status))
                return
            if parsed.path == "/api/demos/claude-steer/status":
                with claude_steer_lock:
                    self._send_json(dict(claude_steer_status))
                return
            if parsed.path == "/api/simple-task/status":
                with simple_task_lock:
                    self._send_json(dict(simple_task_status))
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
            if parsed.path == "/api/file":
                query = parse_qs(parsed.query)
                raw_path = query.get("path", [""])[0]
                with WorkforceRuntime(db) as runtime:
                    allowed = _known_file_paths(runtime.store)
                path = Path(raw_path)
                if str(path) not in allowed:
                    self.send_error(HTTPStatus.FORBIDDEN, "file is not a recorded runtime file")
                    return
                self._send_file(path, content_type="text/plain; charset=utf-8")
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
            if parsed.path == "/api/clarifications":
                query = parse_qs(parsed.query)
                only_pending = query.get("pending", ["0"])[0] in {"1", "true", "yes"}
                status, payload = list_clarifications_payload(only_pending=only_pending)
                self._send_json(payload, status=status)
                return
            if parsed.path == "/healthz":
                self._send_json({"ok": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/demos/long-rfc/start":
                status, payload = start_long_rfc_demo(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/demos/real-llm-benchmark/start":
                status, payload = start_real_llm_benchmark(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/demos/claude-steer/start":
                status, payload = start_claude_steer_demo(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/simple-task/start":
                status, payload = start_simple_task(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/designed-task/design":
                status, payload = design_task_config(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/designed-task/start":
                status, payload = start_designed_task(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/runtime-config":
                status, payload = update_runtime_config(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/settings/mcp/servers":
                status, payload = save_mcp_server_from_dashboard(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/settings/mcp/oauth/start":
                status, payload = start_mcp_oauth_from_dashboard(self._read_json_body(), self._oauth_callback_base_url())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/settings/skills":
                status, payload = create_skill_from_dashboard(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/settings/skills/assignments":
                status, payload = assign_skill_from_dashboard(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/tasks/export-trace":
                status, payload = export_task_trace_from_dashboard(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/agents/steer":
                status, payload = steer_agent_from_dashboard(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/rename"):
                task_id = unquote(parsed.path.removeprefix("/api/tasks/").removesuffix("/rename"))
                status, payload = rename_task_from_dashboard(task_id, self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path.startswith("/api/clarifications/") and parsed.path.endswith("/answer"):
                clarification_id = unquote(
                    parsed.path.removeprefix("/api/clarifications/").removesuffix("/answer")
                )
                status, payload = answer_clarification_from_dashboard(clarification_id, self._read_json_body())
                self._send_json(payload, status=status)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/settings/mcp/servers/"):
                server_id = unquote(parsed.path.removeprefix("/api/settings/mcp/servers/"))
                status, payload = delete_mcp_server_from_dashboard(server_id)
                self._send_json(payload, status=status)
                return
            if parsed.path.startswith("/api/tasks/"):
                task_id = unquote(parsed.path.removeprefix("/api/tasks/"))
                status, payload = delete_task_from_dashboard(task_id)
                self._send_json(payload, status=status)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode()
            if not raw.strip():
                return {}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_json(self, payload: dict[str, Any], *, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
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

        def _oauth_callback_base_url(self) -> str:
            protocol = str(self.headers.get("X-Forwarded-Proto") or "http").split(",", 1)[0].strip() or "http"
            host_header = str(self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",", 1)[0].strip()
            if not host_header:
                bound_host, bound_port = self.server.server_address[:2]
                display_host = "127.0.0.1" if bound_host in {"", "0.0.0.0", "::"} else str(bound_host)
                host_header = f"{display_host}:{bound_port}"
            return f"{protocol}://{host_header}/api/settings/mcp/oauth/callback"

        def _send_html(self, html: str, *, status: int = HTTPStatus.OK) -> None:
            body = html.encode()
            self.send_response(status)
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

        def _send_file(self, path: Path, *, content_type: str, cache_control: str = "public, max-age=3600") -> None:
            if not path.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), DashboardHandler)


def add_web_dashboard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", dest="dashboard_config_path", type=Path, default=None, help="Path to Workforce Runtime JSON config")


def _agent_runs(events: list[Any]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type not in {
            "worker_run_started",
            "worker_run_finished",
            "agent_run_started",
            "agent_run_finished",
            "agent_run_path_registered",
            "provider_session_registered",
        }:
            continue
        run_id = str(event.payload.get("run_id") or "")
        if not run_id:
            continue
        kind = "worker" if event.event_type.startswith("worker_") else "agent"
        if event.event_type in {"agent_run_path_registered", "provider_session_registered"} and run_id in runs:
            kind = str(runs[run_id].get("kind") or "worker")
        if event.event_type == "provider_session_registered" and run_id not in runs:
            kind = "worker"
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
                "run_dir": "",
                "stdout_path": "",
                "stderr_path": "",
                "prompt_path": "",
                "response_path": "",
                "raw_response_path": "",
                "error_path": "",
                "last_attempt_error_path": "",
                "provider": "",
                "provider_session_id": "",
                "resume_command": "",
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
        elif event.event_type == "agent_run_path_registered":
            run["run_dir"] = str(event.payload.get("run_dir") or "")
            run["stdout_path"] = str(event.payload.get("stdout_path") or "")
            run["stderr_path"] = str(event.payload.get("stderr_path") or "")
            run["prompt_path"] = str(event.payload.get("prompt_path") or "")
            run["response_path"] = str(event.payload.get("response_path") or "")
            run["raw_response_path"] = str(event.payload.get("raw_response_path") or "")
            run["error_path"] = str(event.payload.get("error_path") or "")
            run["last_attempt_error_path"] = str(event.payload.get("last_attempt_error_path") or "")
        elif event.event_type == "provider_session_registered":
            run["provider"] = str(event.payload.get("provider") or "")
            run["provider_session_id"] = str(event.payload.get("provider_session_id") or "")
            run["resume_command"] = str(event.payload.get("resume_command") or "")
        else:
            run["status"] = str(event.payload.get("status") or "finished")
            run["error"] = str(event.payload.get("error") or "")
    return list(runs.values())


def _active_worker_run_for_agent(events: list[Any], *, agent_id: str, task_id: str | None = None) -> dict[str, Any] | None:
    for run in reversed(_agent_runs(events)):
        if run.get("kind") != "worker":
            continue
        if run.get("agent_id") != agent_id:
            continue
        if task_id and run.get("task_id") != task_id:
            continue
        if run.get("status") == "running":
            return run
    return None


def _resolve_agent_task_id(runtime: WorkforceRuntime, *, agent_id: str, task_id: str | None = None) -> str | None:
    if task_id:
        return task_id
    agent = runtime.get_agent(agent_id)
    if agent is not None and agent.current_task_ids:
        return agent.current_task_ids[-1]
    for task in reversed(runtime.list_tasks()):
        if task.assigned_to == agent_id:
            return task.task_id
    for event in reversed(runtime.store.list_events()):
        if event.actor_id == agent_id and event.task_id:
            return event.task_id
    return None


def _event_usage(events: list[Any]) -> dict[str, int]:
    actual_tokens = 0
    runtime_seconds = 0
    tool_calls = 0
    output_text = ""
    for event in events:
        if event.event_type in {"agent_run_finished", "worker_run_finished"}:
            usage = event.payload.get("usage")
            if isinstance(usage, dict):
                actual_tokens += _usage_tokens(usage)
                runtime_seconds += _int_value(usage.get("runtime_seconds"))
                tool_calls += _int_value(usage.get("tool_calls"))
        if event.event_type in {"agent_output", "worker_output"}:
            output_text += " " + str(event.payload.get("text") or "")
    return {
        "actual_tokens": actual_tokens,
        "runtime_seconds": runtime_seconds,
        "tool_calls": tool_calls,
        "estimated_tokens": _estimate_tokens(output_text),
    }


def _work_queue_state(items: list[Any], *, item_limit: int = 200) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    active_agents: set[str] = set()
    for item in items:
        status = str(item.status)
        kind = str(item.kind)
        status_counts[status] = status_counts.get(status, 0) + 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if item.is_active():
            active_agents.add(item.agent_id)
    return {
        "total": len(items),
        "status_counts": status_counts,
        "kind_counts": kind_counts,
        "active_agents": len(active_agents),
        "items": [item.model_dump(mode="json") for item in items[-item_limit:]],
        "item_limit": item_limit,
    }


def _usage_tokens(usage: dict[str, Any]) -> int:
    for key in ("total_tokens", "tokens_used"):
        value = _int_value(usage.get(key))
        if value:
            return value
    return (
        _int_value(usage.get("input_tokens"))
        + _int_value(usage.get("output_tokens"))
        + _int_value(usage.get("reasoning_output_tokens"))
        + _int_value(usage.get("prompt_tokens"))
        + _int_value(usage.get("completion_tokens"))
    )


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _estimate_tokens(text: str) -> int:
    if not text.strip():
        return 0
    return max(1, len(text) // 4)


def _agent_output(events: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in events:
        if event.event_type not in {"worker_output", "agent_output"}:
            continue
        output.append(_agent_output_item(event))
    return output


def _trace_files(events: list[Any]) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for event in events:
        if event.event_type not in {"trace_file_written", "task_trace_exported"}:
            continue
        path = str(event.payload.get("trace_path") or "")
        if not path:
            continue
        traces.append(
            {
                "path": path,
                "run_id": event.payload.get("run_id") or "",
                "trace_id": event.payload.get("trace_id") or "",
                "task_id": event.task_id
                or event.payload.get("task_id")
                or event.payload.get("final_task_id")
                or "",
                "label": event.payload.get("label") or ("task" if event.event_type == "task_trace_exported" else ""),
                "timestamp": event.timestamp.isoformat(),
            }
        )
    return traces


def _human_reports(events: list[Any]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for event in events:
        if event.event_type != "human_report_registered":
            continue
        payload = event.payload
        reports.append(
            {
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat(),
                "from_agent_id": event.actor_id,
                "task_id": event.task_id or "",
                "human_report_id": payload.get("human_report_id") or "",
                "title": payload.get("title") or "CEO report to human",
                "message": payload.get("message") or "",
                "status": payload.get("status") or "",
                "confidence": payload.get("confidence"),
                "next_action": payload.get("next_action") or "",
                "requires_decision": bool(payload.get("requires_decision", False)),
            }
        )
    return reports


def _known_file_paths(store: RuntimeStore) -> set[str]:
    paths: set[str] = set()
    for artifact in store.list_artifacts():
        paths.add(str(Path(artifact.path)))
    for event in store.list_events():
        for key in (
            "trace_path",
            "stdout_path",
            "stderr_path",
            "prompt_path",
            "response_path",
            "raw_response_path",
            "error_path",
            "last_attempt_error_path",
        ):
            value = event.payload.get(key)
            if value:
                paths.add(str(Path(str(value))))
        if event.event_type == "agent_run_path_registered" and event.payload.get("run_dir"):
            run_dir = Path(str(event.payload["run_dir"]))
            if run_dir.exists():
                for path in run_dir.iterdir():
                    if path.is_file():
                        paths.add(str(path))
    return paths


def _agent_activity(agents: list[Any], events: list[Any], config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output_limit = _config_int(config, "activity", "recent_output_items", default=12)
    tool_limit = _config_int(config, "activity", "recent_tool_items", default=12)
    event_limit = _config_int(config, "activity", "recent_event_items", default=10)
    full_stream_limit = _config_int(config, "activity", "full_stream_limit", default=200)
    event_scan_limit = _config_int(config, "activity", "event_scan_limit", default=1200)
    activity: dict[str, dict[str, Any]] = {
        agent.id: {"output": [], "full_output": [], "errors": [], "tools": [], "events": []}
        for agent in agents
    }
    for event in events[-event_scan_limit:]:
        if event.event_type == "agent_model_auto_replaced":
            target_agent_id = str(event.payload.get("agent_id") or "")
            if target_agent_id in activity:
                activity[target_agent_id]["output"] = []
                activity[target_agent_id]["full_output"] = []
                activity[target_agent_id]["errors"] = []
                activity[target_agent_id]["events"].append(_activity_event_item(event))
            continue
        if event.event_type == "agent_models_migrated":
            sample = event.payload.get("sample") if isinstance(event.payload.get("sample"), list) else []
            for item in sample:
                if not isinstance(item, dict):
                    continue
                target_agent_id = str(item.get("id") or "")
                if target_agent_id in activity:
                    activity[target_agent_id]["output"] = []
                    activity[target_agent_id]["full_output"] = []
                    activity[target_agent_id]["errors"] = []
                    activity[target_agent_id]["events"].append(_activity_event_item(event))
            continue
        if event.actor_id not in activity:
            continue
        agent_activity = activity[event.actor_id]
        if event.event_type in {"worker_output", "agent_output"}:
            item = _agent_output_item(event)
            if item.get("stream") == "error":
                agent_activity["errors"].append(item)
                agent_activity["errors"] = _tail(agent_activity["errors"], output_limit)
            else:
                agent_activity["output"].append(item)
                agent_activity["output"] = _tail(agent_activity["output"], output_limit)
            agent_activity["full_output"].append(item)
            agent_activity["full_output"] = _tail(agent_activity["full_output"], full_stream_limit)
            continue
        if _is_tool_call_event(event.event_type):
            agent_activity["tools"].append(_tool_event_item(event))
            agent_activity["tools"] = _tail(agent_activity["tools"], tool_limit)
            continue
        if event.event_type in {
            "task_created",
            "task_assigned",
            "task_status_updated",
            "discussion_message",
            "report_registered",
            "human_report_registered",
            "manager_review_created",
            "manager_review_decided",
            "progress_checked",
            "agent_hired",
            "agent_profile_updated",
            "system_prompt_updated",
            "task_document_upserted",
            "tool_request_submitted",
            "tool_request_approved",
            "tool_request_rejected",
            "work_item_enqueued",
            "work_item_deduplicated",
            "work_item_claimed",
            "work_item_completed",
            "work_item_failed",
            "work_item_requeued",
            "work_item_cancelled",
            "work_item_lease_expired",
            "human_agent_steer_requested",
            "human_agent_steer_sent",
            "human_agent_steer_failed",
            "human_agent_interrupt_sent",
            "agent_run_path_registered",
            "agent_run_attempt_started",
            "agent_run_attempt_failed",
            "agent_run_retrying",
            "trace_file_written",
            "runtime_config_updated",
        }:
            agent_activity["events"].append(_activity_event_item(event))
            agent_activity["events"] = _tail(agent_activity["events"], event_limit)
    by_agent = {agent.id: agent for agent in agents}
    for agent_id, agent_activity in activity.items():
        agent_activity["summary"] = _agent_activity_summary(by_agent[agent_id], agent_activity, config)
    return activity


def _agent_payload(agent: Any) -> dict[str, Any]:
    payload = agent.model_dump(mode="json")
    payload["has_system_prompt"] = bool(payload.get("system_prompt"))
    payload["system_prompt"] = ""
    return payload


def _org_chart(
    agents: list[Any],
    activity: dict[str, dict[str, Any]],
    config: dict[str, Any],
    *,
    max_nodes: int = 0,
) -> list[dict[str, Any]]:
    by_manager: dict[str | None, list[Any]] = {}
    for agent in agents:
        by_manager.setdefault(agent.manager_id, []).append(agent)

    descendant_memo: dict[str, int] = {}

    def descendant_count_for(agent_id: str) -> int:
        if agent_id in descendant_memo:
            return descendant_memo[agent_id]
        children = by_manager.get(agent_id, [])
        count = sum(1 + descendant_count_for(child.id) for child in children)
        descendant_memo[agent_id] = count
        return count

    visible_count = 0

    def placeholder(parent_id: str, hidden_count: int) -> dict[str, Any]:
        return {
            "id": f"hidden_{parent_id}",
            "name": f"{hidden_count} agent(s) hidden by dashboard state limit",
            "role": "Hidden agents",
            "department": "",
            "status": "idle",
            "model": "",
            "worker_type": "",
            "placeholder": True,
            "child_count": 0,
            "descendant_count": max(hidden_count - 1, 0),
            "children": [],
            "activity": {"output": [], "full_output": [], "errors": [], "tools": [], "events": []},
            "summary": {"mode": "local", "text": "Hidden by dashboard state limit.", "active": False},
        }

    def build(agent: Any) -> dict[str, Any]:
        nonlocal visible_count
        visible_count += 1
        children: list[dict[str, Any]] = []
        sorted_children = sorted(by_manager.get(agent.id, []), key=lambda item: item.id)
        for index, child in enumerate(sorted_children):
            if max_nodes > 0 and visible_count >= max_nodes:
                hidden_count = sum(1 + descendant_count_for(item.id) for item in sorted_children[index:])
                children.append(placeholder(agent.id, hidden_count))
                break
            children.append(build(child))
        descendant_count = descendant_count_for(agent.id)
        return {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "department": agent.department,
            "status": agent.status,
            "model": agent.model,
            "has_system_prompt": bool(agent.system_prompt),
            "system_prompt": "",
            "model_capabilities": model_capabilities(agent.model, {"models": config.get("models", {})}) or {},
            "worker_type": agent.worker_type,
            "icon": _agent_icon(agent, config),
            "current_task_ids": list(agent.current_task_ids),
            "activity": activity.get(agent.id, {"output": [], "full_output": [], "errors": [], "tools": [], "events": []}),
            "summary": activity.get(agent.id, {}).get("summary", {}),
            "child_count": len(children),
            "descendant_count": descendant_count,
            "children": children,
        }

    roots: list[dict[str, Any]] = []
    sorted_roots = sorted(by_manager.get(None, []), key=lambda item: item.id)
    for index, root in enumerate(sorted_roots):
        if max_nodes > 0 and visible_count >= max_nodes:
            hidden_count = sum(1 + descendant_count_for(item.id) for item in sorted_roots[index:])
            roots.append(placeholder("root", hidden_count))
            break
        roots.append(build(root))
    return roots


def _agent_activity_summary(agent: Any, activity: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    max_chars = _config_int(config, "summaries", "max_chars", default=140)
    items: list[dict[str, str]] = []
    output_items = activity.get("full_output", []) or activity.get("output", [])
    for output in _aggregate_output_items(output_items)[-1:]:
        stream = output.get("stream") or "output"
        label = "Error" if stream == "error" else str(stream)
        output_limit = max(12, max_chars - len(label) - 2)
        text = _single_line_tail(str(output.get("text") or ""), output_limit)
        if text:
            items.append(
                {
                    "timestamp": str(output.get("timestamp") or ""),
                    "event_type": str(output.get("event_type") or "output"),
                    "text": f"{label}: {text}",
                    "full_text": f"{label}: {_single_line(str(output.get('text') or ''), 100000)}",
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
        full_text = latest.get("full_text", text)
        task_id = latest["task_id"]
        event_type = latest["event_type"]
        timestamp = latest["timestamp"]
    elif agent.current_task_ids:
        text = f"Working on {', '.join(agent.current_task_ids)}"
        full_text = text
        task_id = agent.current_task_ids[0]
        event_type = ""
        timestamp = ""
    elif agent.status == "idle":
        text = "Idle."
        full_text = text
        task_id = ""
        event_type = ""
        timestamp = ""
    else:
        text = f"{agent.status}."
        full_text = text
        task_id = ""
        event_type = ""
        timestamp = ""
    return {
        "mode": "local",
        "requested_mode": str(config.get("summaries", {}).get("mode") or "local"),
        "text": _single_line(text, max_chars),
        "full_text": full_text,
        "task_id": task_id,
        "event_type": event_type,
        "updated_at": timestamp,
        "active": active,
    }


def _aggregate_output_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for item in items:
        if groups and _same_output_stream(groups[-1], item):
            groups[-1]["text"] = str(groups[-1].get("text") or "") + str(item.get("text") or "")
            groups[-1]["timestamp"] = item.get("timestamp") or groups[-1].get("timestamp")
            groups[-1]["event_id"] = item.get("event_id") or groups[-1].get("event_id")
        else:
            copy = dict(item)
            copy["text"] = str(copy.get("text") or "")
            groups.append(copy)
    return groups


def _same_output_stream(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(left.get("agent_id") or "") == str(right.get("agent_id") or "")
        and str(left.get("task_id") or "") == str(right.get("task_id") or "")
        and str(left.get("run_id") or "") == str(right.get("run_id") or "")
        and str(left.get("stream") or "output") == str(right.get("stream") or "output")
    )


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
    status = event.event_type.removeprefix("mcp_tool_call_")
    if status == event.event_type:
        status = event.event_type.removeprefix("tool_call_")
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "task_id": event.task_id,
        "agent_id": event.actor_id,
        "tool_name": payload.get("tool_name"),
        "status": status,
        "target_agent_id": payload.get("target_agent_id")
        or payload.get("to_agent_id")
        or payload.get("assigned_to")
        or payload.get("worker_id"),
        "message": payload.get("message") or payload.get("title") or payload.get("error") or payload.get("url") or "",
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


def _prune_pending_mcp_oauth(pending: dict[str, dict[str, Any]], *, max_age_seconds: int = 900) -> None:
    cutoff = time.time() - max_age_seconds
    for state, item in list(pending.items()):
        created_at = item.get("created_at")
        if not isinstance(created_at, float | int) or created_at < cutoff:
            pending.pop(state, None)


def _first_query_param(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def _oauth_callback_html(title: str, message: str, ok: bool) -> str:
    color = "#2f7a4d" if ok else "#a33a32"
    escaped_title = html_lib.escape(title)
    escaped_message = html_lib.escape(message)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
      background: #f4f3f0;
      color: #1c1b19;
    }}
    main {{
      width: min(480px, calc(100vw - 40px));
      border: 1px solid #e3e0da;
      border-radius: 14px;
      background: #fff;
      padding: 28px;
      box-shadow: 0 1px 2px rgba(28, 27, 25, .06);
    }}
    .dot {{
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: {color};
      margin-bottom: 18px;
    }}
    h1 {{
      margin: 0 0 10px 0;
      font-size: 22px;
      letter-spacing: -.02em;
    }}
    p {{
      margin: 0;
      color: #6f6a61;
      line-height: 1.55;
    }}
  </style>
</head>
<body>
  <main>
    <div class="dot"></div>
    <h1>{escaped_title}</h1>
    <p>{escaped_message}</p>
  </main>
</body>
</html>"""


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
        "requested_tool_name",
        "assigned_to",
        "to_agent_id",
        "target_agent_id",
        "status",
        "stream",
        "returncode",
        "timed_out",
        "report_id",
        "human_report_id",
        "decision",
        "message",
        "title",
        "url",
        "doc_id",
        "doc_type",
        "request_id",
        "approval_level",
        "problem",
        "trace_path",
        "run_dir",
        "stdout_path",
        "stderr_path",
        "prompt_path",
        "response_path",
        "raw_response_path",
        "error_path",
        "last_attempt_error_path",
        "attempt",
        "max_attempts",
        "next_attempt",
        "delay_seconds",
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


def _is_tool_call_event(event_type: str) -> bool:
    return event_type.startswith("mcp_tool_call_") or event_type.startswith("tool_call_")


def _single_line(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _single_line_tail(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return "..." + compact[-max(limit - 3, 1) :]


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _payload_int(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def _optional_positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _string_list(value: object, *, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = [str(value).strip()]
    cleaned = [item for item in items if item]
    return cleaned or list(default or [])


# Dashboard UI is built from workforce_runtime/dashboard/frontend into dashboard/static.
