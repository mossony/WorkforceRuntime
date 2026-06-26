from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
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
from workforce_runtime.llm import RoutedLLMClient
from workforce_runtime.org_designer import OrgDesigner, OrgDesignRequest
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
DEFAULT_REAL_LLM_BENCHMARK_CASE = Path("examples/benchmarks/web_research_real_llm.json")


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
        use_llm = bool(payload.get("use_llm", True))
        request = OrgDesignRequest(
            goal=goal,
            company_name=str(payload.get("company_name") or designed_defaults.get("company_name") or "Designed Task Workforce"),
            headcount_limit=headcount_limit,
            token_budget=token_budget,
            management_model=management_model,
            worker_model=worker_model,
        )
        organization = OrgDesigner().design(request, use_llm=use_llm, allow_fallback=True)
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
                    agent = runtime.get_agent(agent_id)
                    if agent is not None and agent.worker_type.startswith("openrouter_"):
                        effective_task_id = _resolve_agent_task_id(runtime, agent_id=agent_id, task_id=task_id)
                        thread = threading.Thread(
                            target=run_idle_openrouter_steer_background,
                            args=(agent_id, effective_task_id, message, from_agent_id),
                            daemon=True,
                        )
                        thread.start()
                        return HTTPStatus.ACCEPTED, {
                            "ok": True,
                            "status": "idle_chat_started",
                            "agent_id": agent_id,
                            "task_id": effective_task_id or "",
                            "message": "No live session; started an idle OpenRouter chat turn for this agent.",
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

    def run_idle_openrouter_steer_background(
        agent_id: str,
        task_id: str | None,
        message: str,
        from_agent_id: str,
    ) -> None:
        run_id = f"run_idle_steer_{uuid4().hex[:12]}"
        try:
            with WorkforceRuntime(db) as runtime:
                agent = runtime.get_agent(agent_id)
                if agent is None:
                    runtime.record_event(
                        event_type="human_agent_steer_failed",
                        actor_id=from_agent_id,
                        task_id=task_id,
                        payload={"target_agent_id": agent_id, "message": message, "action": "idle_chat", "status": "agent_not_found"},
                    )
                    return
                effective_task_id = _resolve_agent_task_id(runtime, agent_id=agent_id, task_id=task_id)
                model = agent.model or "openai/gpt-oss-120b:free"
                attempts = 0
                while attempts < 2:
                    attempts += 1
                    runtime.record_agent_run_started(
                        run_id=run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                        task_id=effective_task_id,
                        actor_id=agent_id,
                        adapter="dashboard-idle-steer",
                        model=model,
                    )
                    chunks: list[str] = []

                    def on_delta(text: str) -> None:
                        chunks.append(text)
                        runtime.record_agent_output(
                            run_id=run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                            task_id=effective_task_id,
                            actor_id=agent_id,
                            stream="assistant",
                            text=text,
                        )

                    try:
                        response = RoutedLLMClient().chat(
                            model=model,
                            messages=[
                                {"role": "system", "content": agent.system_prompt or f"You are {agent.name}, {agent.role}."},
                                {"role": "user", "content": _idle_steer_user_prompt(runtime, agent_id, effective_task_id, message)},
                            ],
                            temperature=0.2,
                            max_tokens=1200,
                            reasoning=True,
                            stream=True,
                            on_delta=on_delta,
                        )
                        if response.content and not chunks:
                            runtime.record_agent_output(
                                run_id=run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                                task_id=effective_task_id,
                                actor_id=agent_id,
                                stream="assistant",
                                text=response.content,
                            )
                        runtime.record_agent_run_finished(
                            run_id=run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                            task_id=effective_task_id,
                            actor_id=agent_id,
                            status="completed",
                            usage=response.usage,
                        )
                        runtime.record_event(
                            event_type="human_agent_steer_sent",
                            actor_id=from_agent_id,
                            task_id=effective_task_id,
                            payload={
                                "target_agent_id": agent_id,
                                "message": message,
                                "action": "idle_chat",
                                "run_id": run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                                "model": model,
                            },
                        )
                        return
                    except Exception as exc:  # noqa: BLE001 - visible dashboard failure with possible model failover.
                        error = str(exc)
                        runtime.record_agent_output(
                            run_id=run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                            task_id=effective_task_id,
                            actor_id=agent_id,
                            stream="error",
                            text=error,
                        )
                        runtime.record_agent_run_finished(
                            run_id=run_id if attempts == 1 else f"{run_id}_retry_{attempts}",
                            task_id=effective_task_id,
                            actor_id=agent_id,
                            status="failed",
                            error=error,
                        )
                        replacement = runtime.auto_replace_unavailable_agent_model(
                            agent_id=agent_id,
                            failed_model=model,
                            error=error,
                            task_id=effective_task_id,
                            actor_id="dashboard",
                        )
                        if replacement is None or not replacement.model or replacement.model == model:
                            runtime.record_event(
                                event_type="human_agent_steer_failed",
                                actor_id=from_agent_id,
                                task_id=effective_task_id,
                                payload={
                                    "target_agent_id": agent_id,
                                    "message": message,
                                    "action": "idle_chat",
                                    "status": "failed",
                                    "error": error,
                                },
                            )
                            return
                        agent = replacement
                        model = replacement.model
        except Exception as exc:  # noqa: BLE001 - thread should not fail silently.
            with WorkforceRuntime(db) as runtime:
                runtime.record_event(
                    event_type="human_agent_steer_failed",
                    actor_id=from_agent_id,
                    task_id=task_id,
                    payload={
                        "target_agent_id": agent_id,
                        "message": message,
                        "action": "idle_chat",
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
            result = run_benchmark_case(
                db,
                workspace=workspace,
                case=case,
                use_llm=use_llm,
                judge=judge if judge in {"none", "heuristic", "llm"} else "heuristic",  # type: ignore[arg-type]
                reset=reset,
                organization_override=organization,
                llm_json_config=runtime_config.get("benchmarks", {}).get("llm_json"),
                source_excerpt_chars=int(runtime_config.get("benchmarks", {}).get("source_excerpt_chars", 20000)),
                on_root_task_created=mark_root_task_created,
            )
            with designed_task_lock:
                if designed_task_status.get("run_id") == run_id:
                    designed_task_status = {
                        **designed_task_status,
                        "running": False,
                        "status": "completed",
                        "finished_at": _timestamp(),
                        "root_task_id": result.root_task_id,
                        "result": result.model_dump(mode="json"),
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
            if parsed.path == "/api/tasks/export-trace":
                status, payload = export_task_trace_from_dashboard(self._read_json_body())
                self._send_json(payload, status=status)
                return
            if parsed.path == "/api/agents/steer":
                status, payload = steer_agent_from_dashboard(self._read_json_body())
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


def _idle_steer_user_prompt(runtime: WorkforceRuntime, agent_id: str, task_id: str | None, message: str) -> str:
    parts = [
        "The human operator is sending you a steering/chat message while you are idle.",
        "Answer as this Workforce Runtime agent. Be concise, explicit, and ground your answer in the available task context.",
        "",
        f"Human message:\n{message}",
    ]
    if task_id:
        task = runtime.store.get_task(task_id)
        if task is not None:
            parts.extend(["", f"Current task:\n{task.model_dump_json(indent=2)}"])
        reports = runtime.store.list_reports_by_task(task_id)[-8:]
        if reports:
            parts.extend(
                [
                    "",
                    "Recent reports:",
                    json.dumps(
                        [
                            {
                                "from": report.from_agent_id,
                                "to": report.to_agent_id,
                                "status": report.status,
                                "summary": report.summary,
                                "work_done": report.work_done[:5],
                                "risks": report.risks[:5],
                            }
                            for report in reports
                        ],
                        indent=2,
                    ),
                ]
            )
        documents = runtime.store.list_task_documents_by_task(task_id)[-5:]
        if documents:
            parts.extend(
                [
                    "",
                    "Recent task documents:",
                    json.dumps(
                        [
                            {
                                "title": doc.title,
                                "doc_type": doc.doc_type,
                                "content": doc.content[:2000],
                            }
                            for doc in documents
                        ],
                        indent=2,
                    ),
                ]
            )
    recent_events = [
        {
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "task_id": event.task_id,
            "payload": event.payload,
        }
        for event in runtime.store.list_events()[-80:]
        if event.actor_id == agent_id or (task_id and event.task_id == task_id)
    ][-20:]
    if recent_events:
        parts.extend(["", "Recent relevant events:", json.dumps(recent_events, indent=2)[:12000]])
    return "\n".join(parts)


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


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Workforce Runtime</title>
  <link rel="icon" href="data:,">
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", system-ui, sans-serif;
      background: #f4f3f0; color: #1c1b19;
      -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
    }
    body.detail-open { overflow: hidden; }
    /* scrollbar */
    .wr-scroll::-webkit-scrollbar { width: 9px; }
    .wr-scroll::-webkit-scrollbar-thumb { background: #d6d2cb; border-radius: 6px; border: 3px solid transparent; background-clip: padding-box; }
    .wr-scroll::-webkit-scrollbar-thumb:hover { background: #c3beb5; background-clip: padding-box; }
    .wr-scroll::-webkit-scrollbar-track { background: transparent; }
    /* animations */
    @keyframes wrPulse { 0%,100%{opacity:1;transform:scale(1);}50%{opacity:.35;transform:scale(.82);} }
    @keyframes wrFlow { 0%{background-position:0 0;}100%{background-position:28px 0;} }
    @keyframes wrSpin { to { transform: rotate(360deg); } }
    @keyframes pulse-fade { 0%{transform:translateY(6px);opacity:0;}12%{transform:translateY(0);opacity:1;}82%{transform:translateY(0);opacity:1;}100%{transform:translateY(-4px);opacity:0;} }
    @keyframes progress-slide { 0%{transform:translateX(-105%);}55%{transform:translateX(120%);}100%{transform:translateX(120%);} }
    /* shell */
    #app-shell { display:flex; height:100vh; width:100%; overflow:hidden; background:#f4f3f0; }
    /* sidebar */
    #sidebar { flex:0 0 264px; width:264px; height:100%; background:#efedea; border-right:1px solid #e3e0da; display:flex; flex-direction:column; transition:width .22s cubic-bezier(.4,0,.2,1),flex-basis .22s cubic-bezier(.4,0,.2,1); }
    #sidebar.collapsed { width:56px; flex-basis:56px; }
    .sb-header { padding:14px 14px 10px 14px; display:flex; align-items:center; gap:8px; }
    .sb-mode-wrap { flex:1; display:flex; position:relative; background:#e4e1db; border:1px solid #dcd8d1; border-radius:9px; padding:3px; }
    .sb-mode-slider { position:absolute; top:3px; bottom:3px; width:calc(50% - 3px); background:#fff; border-radius:7px; box-shadow:0 1px 2px rgba(28,27,25,.10); transition:transform .22s cubic-bezier(.4,0,.2,1); }
    .sb-mode-wrap button { position:relative; z-index:1; flex:1; border:0; background:transparent; cursor:pointer; padding:6px 0; font-size:12.5px; font-weight:600; letter-spacing:.01em; font-family:inherit; display:flex; align-items:center; justify-content:center; gap:5px; }
    .sb-collapse-btn { flex:0 0 auto; width:32px; height:32px; border:1px solid #dcd8d1; background:#e4e1db; border-radius:8px; cursor:pointer; display:flex; align-items:center; justify-content:center; color:#57544e; font-family:inherit; }
    .sb-collapse-btn:hover { background:#dcd8d1; }
    .sb-new-task { margin:2px 14px 12px 14px; width:calc(100% - 28px); border:0; background:#1f1e1b; color:#f6f5f3; border-radius:9px; padding:10px 12px; font-size:13.5px; font-weight:600; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:7px; font-family:inherit; box-shadow:0 1px 2px rgba(28,27,25,.18); }
    .sb-new-task:hover { background:#000; }
    .sb-search-wrap { padding:0 14px 8px 14px; }
    .sb-search { display:flex; align-items:center; background:#e7e4de; border:1px solid #ddd9d2; border-radius:8px; padding:6px 9px; gap:7px; }
    .sb-search input { flex:1; border:0; background:transparent; outline:none; font-size:12.5px; color:#1c1b19; font-family:inherit; min-width:0; }
    .sb-search-count { font-size:10.5px; color:#a9a49b; font-variant-numeric:tabular-nums; }
    .sb-section-title { padding:0 16px 8px 16px; font-size:11px; font-weight:700; color:#6f6a61; text-transform:uppercase; }
    #sidebar-tasks { flex:1; overflow-y:auto; padding:0 8px 16px 8px; }
    .task-group-label { padding:12px 8px 5px 8px; font-size:10.5px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:#a39e95; }
    .task-item { width:100%; text-align:left; border:0; background:transparent; border-radius:7px; padding:7px 9px; margin-bottom:1px; cursor:pointer; display:flex; align-items:center; gap:9px; font-family:inherit; font-size:12.5px; color:#46443e; font-weight:450; }
    .task-item:hover { background:#e5e2db; }
    .task-item.selected { background:#e0ddd5; color:#1c1b19; font-weight:600; }
    .task-item-dot { flex:0 0 auto; width:7px; height:7px; }
    .task-item-name { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sb-footer { padding:10px 16px; border-top:1px solid #e3e0da; display:flex; align-items:center; gap:9px; flex-shrink:0; }
    .sb-avatar { width:26px; height:26px; border-radius:7px; background:#1f1e1b; color:#f6f5f3; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex-shrink:0; }
    .sb-name { font-size:12px; font-weight:600; color:#2a2823; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sb-sub { font-size:10.5px; color:#a39e95; white-space:nowrap; }
    /* collapsed sidebar */
    #sidebar.collapsed .sb-mode-wrap { display:none; }
    #sidebar.collapsed .sb-new-task-text { display:none; }
    #sidebar.collapsed .sb-search-wrap { display:none; }
    #sidebar.collapsed .sb-section-title { display:none; }
    #sidebar.collapsed .task-group-label { display:none; }
    #sidebar.collapsed .task-item-name { display:none; }
    #sidebar.collapsed .sb-name, #sidebar.collapsed .sb-sub { display:none; }
    #sidebar.collapsed .sb-new-task { width:34px; height:34px; padding:0; border-radius:9px; margin:2px auto 8px auto; }
    #sidebar.collapsed #sidebar-tasks { align-items:center; display:flex; flex-direction:column; padding:4px 0; gap:4px; }
    #sidebar.collapsed .task-item { width:32px; height:32px; padding:0; justify-content:center; border-radius:8px; }
    #sidebar.collapsed .sb-footer { justify-content:center; padding:10px; }
    /* main */
    #main { flex:1; min-width:0; height:100%; display:flex; flex-direction:column; }
    /* status bar */
    #status-bar { flex:0 0 auto; padding:12px 28px 0 28px; }
    .sbar-inner { display:flex; align-items:stretch; background:#fff; border:1px solid #e6e3de; border-radius:11px; box-shadow:0 1px 2px rgba(28,27,25,.03); overflow:hidden; }
    .sbar-company { display:flex; align-items:center; gap:9px; padding:0 16px; border-right:1px solid #eceae5; flex-shrink:0; }
    .sbar-pulse-wrap { position:relative; display:flex; width:8px; height:8px; }
    .sbar-pulse { position:absolute; inset:0; border-radius:50%; background:#4a8b63; animation:wrPulse 1.8s ease-in-out infinite; }
    .sbar-company-name { font-size:12.5px; font-weight:650; color:#2a2823; line-height:1.15; }
    .sbar-company-sub { font-size:10px; color:#a39e95; letter-spacing:.04em; text-transform:uppercase; }
    #status-metrics { flex:1; display:flex; align-items:stretch; overflow-x:auto; }
    .sbar-metric { flex:1; min-width:96px; padding:9px 16px; border-right:1px solid #eceae5; display:flex; flex-direction:column; gap:2px; }
    .sbar-metric:last-child { border-right:0; }
    .sbar-metric-lrow { display:flex; align-items:center; gap:6px; }
    .sbar-metric-dot { width:6px; height:6px; border-radius:50%; }
    .sbar-metric-label { font-size:10px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:#a39e95; }
    .sbar-metric-val { font-size:18px; font-weight:660; letter-spacing:-.01em; font-variant-numeric:tabular-nums; transition:color .3s ease; }
    /* main scroll */
    #main-scroll { flex:1; overflow-y:auto; padding:0 28px 60px 28px; }
    #main-content { max-width:860px; margin:0 auto; }
    /* home title */
    .home-eyebrow { font-size:11px; font-weight:600; letter-spacing:.13em; text-transform:uppercase; color:#a39e95; margin-bottom:11px; }
    .home-h1 { margin:0; font-size:27px; font-weight:670; letter-spacing:-.022em; color:#1c1b19; }
    .home-desc { margin:9px 0 0 0; font-size:14px; color:#76726b; line-height:1.5; max-width:560px; }
    /* input card */
    .input-card { background:#fff; border-radius:14px; overflow:hidden; transition:border-color .18s ease,box-shadow .18s ease; }
    .input-card.idle { border:1.5px solid #e6e3de; box-shadow:0 1px 2px rgba(28,27,25,.03); }
    .input-card.focused { border:1.5px solid #7fb094; box-shadow:0 0 0 4px rgba(74,139,99,.12),0 1px 2px rgba(28,27,25,.04); }
    .input-top { display:flex; align-items:flex-start; gap:12px; padding:16px 16px 4px 16px; }
    .input-attach { flex:0 0 auto; width:34px; height:34px; border-radius:9px; border:1px solid #e4e1db; background:#f7f6f3; color:#57544e; cursor:pointer; display:flex; align-items:center; justify-content:center; margin-top:2px; }
    .input-attach:hover { background:#efedea; color:#1c1b19; }
    #designed-task-goal { flex:1; border:0; outline:none; resize:none; background:transparent; font-family:inherit; font-size:15px; line-height:1.55; color:#1c1b19; padding:7px 0; min-height:48px; max-height:200px; }
    .input-examples { display:flex; flex-wrap:wrap; gap:7px; padding:4px 16px 4px 62px; }
    .example-chip { border:1px solid #e6e3de; background:#faf9f7; color:#6b675f; border-radius:7px; padding:5px 10px; font-size:12px; cursor:pointer; font-family:inherit; display:flex; align-items:center; gap:6px; }
    .example-chip:hover { background:#f1efeb; color:#3a3833; border-color:#ddd9d2; }
    .input-footer { display:flex; align-items:center; justify-content:space-between; padding:10px 16px; margin-top:6px; border-top:1px solid #f0eee9; }
    .input-footer-left { display:flex; align-items:center; gap:8px; }
    .input-type-badge { display:inline-flex; align-items:center; gap:6px; background:#f3f1ec; border:1px solid #e6e3de; color:#6b675f; border-radius:7px; padding:5px 9px; font-size:11.5px; font-weight:550; }
    #input-char-count { font-size:11.5px; color:#b3aea4; }
    #design-task-config { border:0; background:#3f7d57; color:#fff; border-radius:9px; padding:9px 18px; font-size:13.5px; font-weight:650; cursor:pointer; display:flex; align-items:center; gap:8px; font-family:inherit; box-shadow:0 1px 2px rgba(40,90,60,.22); transition:background .16s ease,opacity .16s ease; }
    #design-task-config:hover:not(:disabled) { filter:brightness(1.05); }
    #design-task-config:disabled { opacity:.65; cursor:default; }
    #design-task-config.running { background:#6f8a78; opacity:.85; }
    /* pipeline */
    #pipeline-card { margin-top:14px; border-radius:12px; padding:15px 18px; transition:background .25s ease,border-color .25s ease; }
    .pipe-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:14px; }
    .pipe-head-left { display:flex; align-items:flex-start; gap:11px; }
    .pipe-icon { flex:0 0 auto; width:30px; height:30px; border-radius:8px; display:flex; align-items:center; justify-content:center; margin-top:1px; }
    .pipe-title { font-size:13.5px; font-weight:640; }
    .pipe-sub { font-size:12.5px; color:#86827a; margin-top:2px; }
    .pipe-head-right { display:flex; align-items:center; gap:9px; }
    .pipe-badge { font-size:10.5px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; border-radius:6px; padding:3px 8px; }
    .pipe-retry { border-radius:8px; padding:6px 12px; font-size:12.5px; font-weight:600; cursor:pointer; font-family:inherit; display:flex; align-items:center; gap:6px; }
    .pipe-steps { display:flex; align-items:center; }
    .pipe-step-wrap { display:flex; align-items:center; }
    .pipe-step-col { display:flex; flex-direction:column; align-items:center; gap:6px; flex:0 0 auto; }
    .pipe-dot-outer { width:13px; height:13px; border-radius:50%; border:2px solid; display:flex; align-items:center; justify-content:center; transition:all .25s ease; }
    .pipe-dot-inner { width:5px; height:5px; border-radius:50%; }
    .pipe-step-label { font-size:11px; white-space:nowrap; }
    .pipe-line { flex:1; height:2px; margin:0 8px; margin-bottom:18px; border-radius:2px; }
    /* reports */
    #home-reports-section { margin-top:30px; }
    .reports-hdr { display:flex; align-items:center; justify-content:space-between; margin-bottom:13px; }
    .reports-hdr-left { display:flex; align-items:center; gap:9px; }
    .reports-label { font-size:11.5px; font-weight:650; letter-spacing:.08em; text-transform:uppercase; color:#76726b; }
    #reports-count { font-size:11px; font-weight:600; color:#a39e95; background:#eceae5; border-radius:20px; padding:1px 8px; font-variant-numeric:tabular-nums; }
    .reports-from { font-size:11.5px; color:#a39e95; }
    #home-human-reports { display:flex; flex-direction:column; gap:14px; }
    .no-reports-card { background:#fff; border:1px dashed #ddd9d2; border-radius:12px; padding:46px 24px; text-align:center; }
    .no-reports-icon { width:46px; height:46px; border-radius:12px; background:#f3f1ec; display:flex; align-items:center; justify-content:center; margin:0 auto 14px auto; color:#b3aea4; }
    .no-reports-title { font-size:14.5px; font-weight:620; color:#3a3833; }
    .no-reports-desc { margin:7px auto 0 auto; font-size:13px; line-height:1.55; color:#86827a; max-width:380px; }
    /* new report card */
    .wr-report-card { background:#fff; border-radius:12px; box-shadow:0 1px 2px rgba(28,27,25,.04); overflow:hidden; }
    .wr-rh { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:16px 18px 13px 18px; border-bottom:1px solid #f0eee9; }
    .wr-rh-left { display:flex; align-items:flex-start; gap:11px; min-width:0; }
    .wr-avatar { flex:0 0 auto; width:34px; height:34px; border-radius:9px; background:#262420; color:#f3f1ec; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; }
    .wr-rtitle { font-size:15px; font-weight:660; color:#1c1b19; letter-spacing:-.01em; }
    .wr-rmeta { display:flex; align-items:center; gap:7px; margin-top:3px; flex-wrap:wrap; font-size:11.5px; color:#86827a; }
    .wr-rmeta-dot { width:3px; height:3px; border-radius:50%; background:#cfcabf; }
    .wr-rh-right { display:flex; flex-direction:column; align-items:flex-end; gap:7px; flex-shrink:0; }
    .wr-sbadge { display:inline-flex; align-items:center; gap:6px; font-size:11px; font-weight:600; letter-spacing:.03em; border-radius:7px; padding:4px 9px; }
    .wr-sbadge-dot { width:6px; height:6px; border-radius:50%; }
    .wr-conf-row { display:flex; align-items:center; gap:7px; }
    .wr-conf-lbl { font-size:10.5px; color:#a39e95; letter-spacing:.03em; }
    .wr-conf-bar { width:48px; height:5px; border-radius:3px; background:#eceae5; overflow:hidden; }
    .wr-conf-fill { height:100%; border-radius:3px; }
    .wr-conf-val { font-size:11.5px; font-weight:650; color:#3a3833; font-variant-numeric:tabular-nums; }
    .wr-rbody { padding:15px 18px 6px 18px; }
    .wr-slabel { font-size:10.5px; font-weight:650; letter-spacing:.07em; text-transform:uppercase; color:#a39e95; margin-bottom:5px; }
    .wr-summary { margin:0 0 16px 0; font-size:13.5px; line-height:1.62; color:#3a3833; }
    .wr-risks { display:flex; flex-direction:column; gap:7px; margin-bottom:16px; }
    .wr-risk { display:flex; align-items:flex-start; gap:9px; }
    .wr-risk-sev { flex:0 0 auto; margin-top:1px; font-size:9.5px; font-weight:700; letter-spacing:.04em; border-radius:5px; padding:2px 6px; min-width:42px; text-align:center; }
    .wr-risk-text { font-size:13px; line-height:1.55; color:#46443e; }
    .wr-rec-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:4px; }
    .wr-rec-text { margin:0; font-size:13px; line-height:1.55; color:#3a3833; }
    .wr-next-actions { display:flex; flex-direction:column; gap:5px; }
    .wr-na { display:flex; align-items:flex-start; gap:8px; font-size:12.5px; line-height:1.5; color:#46443e; }
    .wr-dec-box { margin:14px 18px 16px 18px; background:#fbf7ee; border:1px solid #ecdcb8; border-radius:10px; padding:13px 15px; }
    .wr-dec-hdr { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
    .wr-dec-label { font-size:11px; font-weight:650; letter-spacing:.06em; text-transform:uppercase; color:#9a6c25; }
    .wr-dec-q { margin:0 0 12px 0; font-size:13.5px; line-height:1.55; color:#5a4d30; font-weight:500; }
    .wr-dec-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
    .wr-dec-primary { border:0; background:#3f7d57; color:#fff; border-radius:8px; padding:8px 15px; font-size:12.5px; font-weight:620; cursor:pointer; font-family:inherit; }
    .wr-dec-alt { border:1px solid #ddd6c4; background:#fff; color:#6b5d3a; border-radius:8px; padding:8px 15px; font-size:12.5px; font-weight:560; cursor:pointer; font-family:inherit; }
    .wr-dec-alt:hover { background:#f7f1e4; }
    /* debug panels */
    #debug-section { display:none; }
    body.mode-debug #debug-section { display:block; }
    body.mode-debug .simple-only { display:none; }
    body.mode-simple .debug-only { display:none; }
    .panel { background:#fff; border:1px solid #e6e3de; border-radius:10px; padding:16px; margin-bottom:14px; }
    .panel h2 { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#76726b; margin:0 0 12px 0; }
    label { display:grid; gap:4px; color:#76726b; font-size:12px; font-weight:600; }
    input, select, textarea { width:100%; border:1px solid #e6e3de; border-radius:7px; padding:7px 9px; color:#1c1b19; background:#fff; font:inherit; font-size:13px; }
    textarea { resize:vertical; }
    .form-row { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; align-items:end; }
    button { font:inherit; cursor:pointer; }
    .pill { display:inline-block; border:1px solid #e6e3de; border-radius:999px; padding:2px 8px; color:#76726b; font-size:12px; background:#faf9f7; }
    .primary-button { background:#3f7d57; color:#fff; border:0; border-radius:8px; padding:8px 14px; font-size:13px; font-weight:650; }
    .primary-button:hover { background:#356b4a; }
    .primary-button:disabled { opacity:.55; cursor:not-allowed; }
    .muted { color:#86827a; font-size:12px; }
    .demo-panel { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .demo-copy { min-width:260px; }
    .demo-title { font-weight:700; margin-bottom:4px; font-size:14px; }
    .demo-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .task-designer { display:grid; gap:10px; }
    .operation-progress { border:1px solid #e6e3de; border-radius:8px; background:#faf9f7; padding:12px; display:grid; gap:9px; }
    .operation-progress.active { border-color:#9fd4b7; background:#f6faf7; }
    .operation-progress-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; flex-wrap:wrap; }
    .operation-progress-title { font-weight:700; font-size:14px; }
    .operation-progress-detail { color:#76726b; font-size:12px; margin-top:2px; }
    .progress-track { height:7px; overflow:hidden; border-radius:999px; background:#e6e3de; }
    .progress-fill { width:32%; height:100%; border-radius:inherit; background:#3f7d57; transform:translateX(-105%); }
    .operation-progress.active .progress-fill { animation:progress-slide 1.25s ease-in-out infinite; }
    .operation-progress.finished .progress-fill { width:100%; transform:translateX(0); animation:none; background:#4a8b63; }
    .operation-progress.failed .progress-fill { width:100%; transform:translateX(0); animation:none; background:#b3524b; }
    .progress-steps { display:flex; flex-wrap:wrap; gap:6px; color:#76726b; font-size:12px; }
    .progress-step { border:1px solid #e6e3de; border-radius:999px; padding:2px 8px; background:#fff; }
    .progress-step.active { color:#3f7d57; border-color:#9fd4b7; background:#ecf4ee; font-weight:700; }
    .draft-org-panel { border:1px solid #e6e3de; border-radius:8px; background:#faf9f7; padding:12px; display:grid; gap:10px; }
    .draft-org-head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
    .draft-org-title { font-weight:700; }
    .draft-tree,.draft-children { list-style:none; padding-left:0; margin:0; }
    .draft-children { margin-left:26px; padding-left:18px; border-left:2px solid #e6e3de; }
    .draft-node { position:relative; margin:10px 0; }
    .draft-children>.draft-node::before { content:""; position:absolute; left:-18px; top:20px; width:16px; border-top:2px solid #e6e3de; }
    .draft-card { display:grid; gap:5px; max-width:560px; border:1px solid #e6e3de; border-radius:8px; background:#fff; padding:9px 10px; }
    .draft-card.executive { border-color:#86bdb7; background:#f3fbfa; }
    .draft-card.manager { border-color:#9bb9ea; background:#f5f8fe; }
    .draft-card.worker { border-color:#c4b5fd; background:#faf7ff; }
    .draft-card.hr { border-color:#f0c27b; background:#fff9ed; }
    .draft-card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; }
    .draft-agent-name { font-weight:700; }
    .draft-agent-role,.draft-agent-meta,.draft-agent-responsibilities { color:#76726b; font-size:12px; }
    .draft-badge { flex:0 0 auto; border:1px solid #e6e3de; border-radius:999px; padding:1px 7px; font-size:11px; color:#76726b; background:#f8fafc; }
    .config-editor { min-height:260px; font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th,td { text-align:left; padding:7px 6px; border-bottom:1px solid #e6e3de; vertical-align:top; }
    th { color:#76726b; font-weight:600; font-size:12px; }
    h1,h2,h3 { margin:0; }
    h3 { font-size:14px; }
    .status { font-weight:650; }
    .completed,.idle,.finished { color:#3f7d57; }
    .failed,.timed_out { color:#b3524b; }
    .busy,.assigned,.in_progress,.running,.blocked { color:#a8742a; }
    pre { margin:8px 0 0; padding:10px; background:#0f172a; color:#e2e8f0; border-radius:6px; overflow:auto; max-height:340px; white-space:pre-wrap; word-break:break-word; font-size:12px; }
    .output-block { border-bottom:1px solid #e6e3de; padding:8px 0; display:grid; gap:6px; }
    .output-text { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12px; line-height:1.45; white-space:pre-wrap; word-break:break-word; color:#263447; }
    .org-toolbar { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; }
    .org-tree,.org-children { list-style:none; padding-left:0; margin:0; }
    .org-children { margin-left:18px; padding-left:14px; border-left:1px solid #e6e3de; }
    .org-node { margin:10px 0; }
    .org-placeholder { border:1px dashed #e6e3de; border-radius:8px; color:#76726b; padding:8px 10px; background:#faf9f7; }
    .agent-node { border:1px solid #e6e3de; border-radius:8px; background:#faf9f7; padding:10px; display:grid; gap:8px; }
    .agent-node.active { border-color:#9fd4b7; background:#f6faf7; }
    .simple-agent-node { border-color:#d8e1ea; background:#fff; box-shadow:0 1px 0 rgba(15,23,42,.03); }
    .simple-agent-node.active { border-color:#66b7ad; background:#f4fbfa; }
    .simple-agent-main { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
    .simple-agent-role { color:#76726b; font-size:12px; margin-top:2px; }
    .simple-agent-work { color:#334155; font-size:12px; }
    .simple-agent-summary { border:1px solid #d8e6e3; border-radius:8px; background:#f8fbfb; padding:8px 9px; display:flex; gap:8px; align-items:center; min-width:0; }
    .simple-agent-summary .summary-text { white-space:normal; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
    .communication-pulses { min-height:34px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:0 0 10px; }
    .communication-pulse { border:1px solid #84b9b3; border-radius:8px; background:#f1fbfa; color:#12312f; padding:7px 9px; font-size:12px; box-shadow:0 4px 12px rgba(17,94,89,.12); animation:pulse-fade 4.5s ease forwards; }
    .communication-pulse.report { border-color:#9fbce4; background:#f4f8ff; color:#1e3a5f; }
    .communication-pulse.human { border-color:#deb36b; background:#fff9ed; color:#6d4a11; }
    .agent-node-head { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
    .agent-title { display:flex; align-items:center; gap:8px; min-width:0; }
    .agent-controls { display:flex; align-items:center; gap:6px; flex:0 0 auto; }
    .tree-toggle { width:30px; height:30px; padding:0; font-weight:800; line-height:1; border:1px solid #e6e3de; background:#fff; border-radius:7px; }
    .agent-icon,.agent-icon-fallback { width:28px; height:28px; border-radius:7px; flex:0 0 auto; }
    .agent-icon { object-fit:cover; border:1px solid #e6e3de; background:#fff; }
    .agent-icon-fallback { display:inline-flex; align-items:center; justify-content:center; border:1px solid #e6e3de; background:#e8eef6; color:#243449; font-size:10px; font-weight:800; }
    .agent-name { font-weight:700; font-size:14px; }
    .agent-meta { color:#76726b; font-size:12px; margin-top:2px; }
    .agent-summary { display:flex; align-items:center; gap:8px; min-width:0; padding:8px; border-radius:7px; background:#eef3f7; color:#1f2f3f; }
    .agent-summary.active { background:#ecf4ee; }
    .summary-dot { width:8px; height:8px; border-radius:999px; background:#a39e95; flex:0 0 auto; }
    .agent-summary.active .summary-dot { background:#4a8b63; }
    .summary-text { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .activity-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; }
    .activity-block { min-width:0; border-top:1px solid #e6e3de; padding-top:6px; }
    .activity-title { color:#76726b; font-size:11px; text-transform:uppercase; margin-bottom:4px; }
    .activity-item { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11px; color:#334155; white-space:pre-wrap; overflow-wrap:anywhere; border-bottom:1px solid #edf0f4; padding:3px 0; }
    .activity-error { color:#9f1239; background:#fff1f2; border:1px solid #fecdd3; border-radius:6px; padding:4px 6px; margin-bottom:4px; }
    .manager-reports { display:grid; gap:10px; }
    .manager-report-card { border:1px solid #e6e3de; border-radius:8px; background:#faf9f7; padding:11px 12px; display:grid; gap:8px; }
    .manager-report-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; flex-wrap:wrap; }
    .manager-report-title { font-weight:800; }
    .manager-report-meta,.manager-report-next,.manager-report-evidence { color:#76726b; font-size:12px; }
    .manager-report-summary { white-space:pre-wrap; overflow-wrap:anywhere; }
    /* detail drawer */
    .detail-backdrop { position:fixed; inset:0; background:rgba(28,27,25,.28); z-index:5; }
    .detail-drawer { position:fixed; top:0; right:0; height:100vh; width:min(760px,94vw); background:#fff; border-left:1px solid #e6e3de; box-shadow:0 18px 60px rgba(28,27,25,.18); z-index:6; transform:translateX(105%); transition:transform 160ms ease; display:grid; grid-template-rows:auto 1fr; }
    body.detail-open .detail-drawer { transform:translateX(0); }
    .detail-head { display:flex; justify-content:space-between; gap:12px; padding:14px 16px; border-bottom:1px solid #e6e3de; }
    .detail-body { overflow:auto; padding:14px 16px 28px; display:grid; gap:12px; }
    .detail-section { border:1px solid #e6e3de; border-radius:8px; padding:10px; }
    .detail-section h3 { margin-bottom:8px; color:#76726b; text-transform:uppercase; font-size:12px; }
    .stream-box { max-height:360px; overflow:auto; border:1px solid #e6e3de; border-radius:6px; padding:0 10px; background:#fcfdff; }
    .detail-body .agent-summary { background:#f3f1ec; }
    .detail-body .agent-summary.active { background:#ecf4ee; }
  </style>
</head>
<body class="mode-simple">
<div id="app-shell">

  <!-- SIDEBAR -->
  <aside id="sidebar">
    <div class="sb-header">
      <div class="sb-mode-wrap" id="sb-mode-wrap">
        <div class="sb-mode-slider" id="sb-mode-slider"></div>
        <button id="mode-simple" data-action="set-dashboard-mode" data-mode="simple" style="color:#1c1b19;">
          <span style="width:5px;height:5px;border-radius:50%;background:#4a8b63;display:inline-block;"></span>Simple
        </button>
        <button id="mode-debug" data-action="set-dashboard-mode" data-mode="debug" style="color:#8a867f;">Debug</button>
      </div>
      <button class="sb-collapse-btn" data-action="toggle-sidebar" title="Collapse">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M6 3.5 2.5 8 6 12.5M13.5 8H3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </div>
    <button class="sb-new-task" data-action="new-task">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      <span class="sb-new-task-text">New task</span>
    </button>
    <div class="sb-search-wrap">
      <div class="sb-search">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style="flex:0 0 auto;color:#8a867f;"><circle cx="7" cy="7" r="4.5" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 10.5 14 14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        <input id="sidebar-search-input" placeholder="Search tasks" oninput="onSidebarSearch(this.value)">
        <span class="sb-search-count" id="sidebar-task-count">0</span>
      </div>
    </div>
    <div class="sb-section-title">Task history</div>
    <div class="wr-scroll" id="sidebar-tasks"></div>
    <div class="sb-footer">
      <div class="sb-avatar" id="company-initials">WR</div>
      <div style="min-width:0;">
        <div class="sb-name" id="mission">Workforce Runtime</div>
        <div class="sb-sub" id="sidebar-sub">0 agents</div>
      </div>
    </div>
  </aside>

  <!-- MAIN -->
  <main id="main">
    <!-- STATUS BAR -->
    <div id="status-bar">
      <div class="sbar-inner">
        <div class="sbar-company">
          <span class="sbar-pulse-wrap"><span class="sbar-pulse"></span></span>
          <div>
            <div class="sbar-company-name" id="company-name-status">Workforce Runtime</div>
            <div class="sbar-company-sub">Operating</div>
          </div>
        </div>
        <div id="status-metrics"></div>
      </div>
    </div>

    <!-- SCROLL -->
    <div class="wr-scroll" id="main-scroll">
      <div id="main-content">

        <!-- TITLE -->
        <div style="padding:48px 0 22px 0;">
          <div class="home-eyebrow">New directive</div>
          <h1 class="home-h1">Where should we begin?</h1>
          <p class="home-desc">Brief your workforce. The organization will design its structure, staff the right agents, execute, and report back with decisions for you.</p>
        </div>

        <!-- INPUT CARD -->
        <div class="input-card idle" id="input-card">
          <div class="input-top">
            <button class="input-attach" title="Add files or context">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 3.2v9.6M3.2 8h9.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
            </button>
            <textarea id="designed-task-goal" rows="2" placeholder="Describe the goal for your workforce..." oninput="onGoalInput(this)" onfocus="onGoalFocus()" onblur="onGoalBlur()"></textarea>
          </div>
          <div class="input-examples">
            <button class="example-chip" onclick="applyExample('Launch the public beta and operate it for 30 days. Prioritize tenant isolation, cost control, and reliability.')">
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M8 1.6 9.9 6l4.5.3-3.5 2.9 1.2 4.4L8 11.2 3.9 13.6l1.2-4.4L1.6 6.3 6.1 6z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>
              Launch the public beta
            </button>
            <button class="example-chip" onclick="applyExample('Rebuild and reconcile the Q3 revenue model against actuals, then prepare it for board reporting.')">
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M8 1.6 9.9 6l4.5.3-3.5 2.9 1.2 4.4L8 11.2 3.9 13.6l1.2-4.4L1.6 6.3 6.1 6z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>
              Rebuild the Q3 revenue model
            </button>
          </div>
          <div class="input-footer">
            <div class="input-footer-left">
              <span class="input-type-badge">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><rect x="2.5" y="2.5" width="11" height="11" rx="2.5" stroke="currentColor" stroke-width="1.3"/></svg>
                New task
              </span>
              <span id="input-char-count"></span>
            </div>
            <button id="design-task-config" data-action="design-task-config">
              <svg id="submit-icon-arrow" width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2.5 8h11M9 3.5 13.5 8 9 12.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <svg id="submit-icon-spin" width="14" height="14" viewBox="0 0 16 16" fill="none" style="display:none;animation:wrSpin .8s linear infinite;"><path d="M8 1.8a6.2 6.2 0 1 1-6.2 6.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
              <span id="submit-label">Design Org</span>
            </button>
          </div>
        </div>

        <!-- PIPELINE -->
        <div id="pipeline-card"></div>

        <!-- HUMAN REPORTS -->
        <div id="home-reports-section">
          <div class="reports-hdr">
            <div class="reports-hdr-left">
              <span class="reports-label">Human Reports</span>
              <span id="reports-count">0</span>
            </div>
            <span class="reports-from" id="reports-from"></span>
          </div>
          <div id="home-human-reports"></div>
        </div>

        <!-- DEBUG PANELS -->
        <div id="debug-section">
          <div class="panel">
            <div class="org-toolbar"><h2>Org Chart</h2><div class="muted" id="org-summary"></div></div>
            <div class="communication-pulses" id="communication-pulses"></div>
            <div id="org-chart"></div>
          </div>
          <div class="panel">
            <h2>Internal Manager Reports</h2>
            <div class="manager-reports" id="manager-reports"></div>
          </div>
          <div class="panel"><h2>Agents</h2><table id="agents"></table></div>
          <div class="panel"><h2>Tasks</h2><table id="tasks"></table></div>
          <div class="panel"><h2>Agent Runs</h2><table id="runs"></table></div>
          <div class="panel"><h2>Reports</h2><table id="reports"></table></div>
          <div class="panel"><h2>Live Agent Output</h2><div id="output"></div></div>
          <div class="panel"><h2>Replay</h2><pre id="replay"></pre></div>
          <div class="panel"><h2>Trajectories</h2><pre id="trajectories"></pre></div>
          <div class="panel">
            <div class="task-designer">
              <div class="demo-panel">
                <div class="demo-copy"><div class="demo-title">Runtime Config</div><div class="muted">Unified Workforce Runtime JSON config.</div></div>
                <div class="demo-actions">
                  <button id="load-runtime-config" data-action="load-runtime-config" class="primary-button">Load Config</button>
                  <button id="save-runtime-config" data-action="save-runtime-config" class="primary-button">Save Config</button>
                  <span class="pill" id="runtime-config-status">config idle</span>
                </div>
              </div>
              <textarea class="config-editor" id="runtime-config-json" spellcheck="false"></textarea>
            </div>
          </div>
          <div class="panel">
            <div class="demo-panel">
              <div class="demo-copy"><div class="demo-title">Long RFC Demo</div></div>
              <div class="demo-actions">
                <button class="primary-button" id="start-long-rfc-demo" data-action="start-long-rfc-demo">Start Long RFC Demo</button>
                <span class="pill" id="long-rfc-demo-status">idle</span>
              </div>
            </div>
          </div>
          <div class="panel">
            <div class="demo-panel">
              <div class="demo-copy"><div class="demo-title">Real LLM Benchmark</div></div>
              <div class="demo-actions">
                <button class="primary-button" id="start-real-llm-benchmark" data-action="start-real-llm-benchmark">Start Real LLM Benchmark</button>
                <span class="pill" id="real-llm-benchmark-status">idle</span>
              </div>
            </div>
          </div>
          <div class="panel">
            <div class="demo-panel">
              <div class="demo-copy"><div class="demo-title">Claude Steer Demo</div></div>
              <div class="demo-actions">
                <button class="primary-button" id="start-claude-steer-demo" data-action="start-claude-steer-demo">Start Claude Steer Demo</button>
                <span class="pill" id="claude-steer-demo-status">idle</span>
              </div>
            </div>
          </div>
          <div class="panel">
            <div class="task-designer">
              <div class="demo-panel">
                <div class="demo-copy"><div class="demo-title">Draft Config / Start Confirmed Task</div></div>
                <div class="demo-actions">
                  <button class="primary-button" id="start-designed-task" data-action="start-designed-task">Start Confirmed Task</button>
                  <label style="display:inline-flex;grid-auto-flow:column;align-items:center;gap:6px;width:auto;">
                    <input id="designed-task-use-llm" type="checkbox" checked style="width:auto;"> use LLM
                  </label>
                  <span class="pill" id="designed-task-status">idle</span>
                  <label style="display:inline-flex;grid-auto-flow:column;align-items:center;gap:6px;width:auto;">
                    Filter task
                    <select id="task-filter-select" style="min-width:260px;"><option value="">All tasks</option></select>
                  </label>
                  <button id="export-task-trace" data-action="export-task-trace">Export Trace</button>
                  <span class="pill" id="task-trace-export-status">trace idle</span>
                </div>
              </div>
              <div class="form-row">
                <label>Headcount<input id="designed-task-headcount" type="number" min="3" value="6"></label>
                <label>Token budget<input id="designed-task-token-budget" type="number" min="0" value="600000"></label>
                <label>Manager model<input id="designed-task-management-model" value="openai/gpt-oss-120b:free"></label>
                <label>Worker model<input id="designed-task-worker-model" value="poolside/laguna-m.1:free"></label>
              </div>
              <div id="designed-task-progress" class="operation-progress" aria-live="polite">
                <div class="operation-progress-head">
                  <div>
                    <div class="operation-progress-title" id="designed-task-progress-title">No design request running.</div>
                    <div class="operation-progress-detail" id="designed-task-progress-detail">Click Design Org to generate a draft organization.</div>
                  </div>
                  <span class="pill" id="designed-task-progress-elapsed">idle</span>
                </div>
                <div class="progress-track"><div class="progress-fill"></div></div>
                <div class="progress-steps" id="designed-task-progress-steps"></div>
              </div>
              <div class="draft-org-panel">
                <div class="draft-org-head"><div class="draft-org-title">Draft Organization Tree</div><span class="pill" id="draft-org-summary">no draft</span></div>
                <div id="draft-org-tree" class="muted">No draft yet.</div>
              </div>
              <label>Editable config JSON<textarea class="config-editor" id="designed-task-config-json" spellcheck="false"></textarea></label>
            </div>
          </div>
        </div>

        <div style="display:none;">
          <span id="stream-status">connecting</span>
          <span id="updated"></span>
          <div id="metrics"></div>
          <div id="human-reports"></div>
        </div>

      </div><!-- #main-content -->
    </div><!-- #main-scroll -->
  </main>
</div><!-- #app-shell -->

  <div class="detail-backdrop" id="agent-backdrop" hidden data-action="close-detail"></div>
  <aside class="detail-drawer" id="agent-detail" aria-hidden="true"></aside>

  <script>
    /* ===== New homepage design glue ===== */
    let sidebarCollapsed = false;
    let sidebarSearchTerm = "";
    let lastAllTasks = [];
    let lastTasks = [];
    let pipelineState = { phase: "idle", state: "idle" };

    function fmt(n) {
      if (n == null || n === "" || (typeof n === "number" && !isFinite(n))) return "-";
      const num = Number(n);
      if (!isFinite(num)) return esc(String(n));
      if (Math.abs(num) >= 1000000) return (num / 1000000).toFixed(num % 1000000 === 0 ? 0 : 1) + "M";
      if (Math.abs(num) >= 1000) return (num / 1000).toFixed(num % 1000 === 0 ? 0 : 1) + "k";
      return String(num);
    }

    function onSidebarSearch(value) {
      sidebarSearchTerm = (value || "").toLowerCase();
      renderSidebarTasks(lastAllTasks, lastTasks);
    }

    function onGoalInput(el) {
      const count = document.getElementById("input-char-count");
      const len = (el.value || "").length;
      if (count) count.textContent = len ? `${len} chars` : "";
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }

    function onGoalFocus() {
      const card = document.getElementById("input-card");
      if (card) { card.classList.remove("idle"); card.classList.add("focused"); }
    }

    function onGoalBlur() {
      const card = document.getElementById("input-card");
      const el = document.getElementById("designed-task-goal");
      if (card && !(el && el.value)) { card.classList.remove("focused"); card.classList.add("idle"); }
      else if (card) { card.classList.remove("focused"); card.classList.add("idle"); }
    }

    function applyExample(text) {
      const el = document.getElementById("designed-task-goal");
      if (!el) return;
      el.value = text;
      el.focus();
      onGoalInput(el);
    }

    function metricColorFor(kind) {
      if (kind === "good") return "#3f7d57";
      if (kind === "bad") return "#b3524b";
      if (kind === "warn") return "#a8742a";
      return "#1c1b19";
    }

    function dotColorFor(kind) {
      if (kind === "good") return "#4a8b63";
      if (kind === "bad") return "#b3524b";
      if (kind === "warn") return "#b07d2f";
      return "#cfcabf";
    }

    function renderStatusBar(data) {
      const host = document.getElementById("status-metrics");
      if (!host) return;
      const company = (data && data.company) || {};
      const nameEl = document.getElementById("company-name-status");
      if (nameEl) nameEl.textContent = company.name || "Workforce Runtime";
      const initialsEl = document.getElementById("company-initials");
      if (initialsEl) {
        const initials = (company.name || "WR").split(/\s+/).map(w => w[0]).filter(Boolean).slice(0, 2).join("").toUpperCase();
        initialsEl.textContent = initials || "WR";
      }
      const tasks = (data && data.tasks) || [];
      const agentsList = (data && data.agents) || [];
      const budget = (data && data.budget) || {};
      const active = tasks.filter(t => ["assigned", "in_progress", "blocked"].includes(t.status)).length;
      const completed = tasks.filter(t => t.status === "completed").length;
      const failed = tasks.filter(t => t.status === "failed").length;
      const busyAgents = agentsList.filter(a => ["busy", "assigned", "in_progress", "running"].includes(a.status)).length;
      const tokensUsed = budget.tokens_used || 0;
      const tokenLimit = budget.token_budget_limit || 0;
      const metrics = [
        { label: "Agents", value: `${agentsList.length}${budget.headcount_limit ? "/" + budget.headcount_limit : ""}`, kind: "neutral" },
        { label: "Working", value: busyAgents, kind: busyAgents ? "warn" : "neutral" },
        { label: "Active", value: active, kind: active ? "warn" : "neutral" },
        { label: "Done", value: completed, kind: completed ? "good" : "neutral" },
        { label: "Failed", value: failed, kind: failed ? "bad" : "neutral" },
        { label: "Tokens", value: tokenLimit ? `${fmt(tokensUsed)}/${fmt(tokenLimit)}` : fmt(tokensUsed), kind: "neutral" },
        { label: "Events", value: fmt((data && (data.agent_output || data.worker_output) || []).length), kind: "neutral" },
      ];
      host.innerHTML = metrics.map(m => `
        <div class="sbar-metric">
          <div class="sbar-metric-lrow">
            <span class="sbar-metric-dot" style="background:${dotColorFor(m.kind)};"></span>
            <span class="sbar-metric-label">${esc(m.label)}</span>
          </div>
          <span class="sbar-metric-val" style="color:${metricColorFor(m.kind)};">${m.value}</span>
        </div>`).join("");
      const sub = document.getElementById("sidebar-sub");
      if (sub) sub.textContent = `${agentsList.length} agent${agentsList.length === 1 ? "" : "s"}`;
    }

    function taskStatusKind(status) {
      if (status === "completed") return "good";
      if (status === "failed" || status === "timed_out") return "bad";
      if (["assigned", "in_progress", "blocked", "running"].includes(status)) return "warn";
      return "neutral";
    }

    function renderSidebarTasks(allTasks, tasks) {
      lastAllTasks = allTasks || [];
      lastTasks = tasks || [];
      const host = document.getElementById("sidebar-tasks");
      if (!host) return;
      const source = (lastAllTasks.length ? lastAllTasks : lastTasks) || [];
      let list = source.slice();
      if (sidebarSearchTerm) {
        list = list.filter(t => `${t.task_id} ${t.title || ""}`.toLowerCase().includes(sidebarSearchTerm));
      }
      const countEl = document.getElementById("sidebar-task-count");
      if (countEl) countEl.textContent = String(list.length);
      const groups = { active: [], completed: [], other: [] };
      for (const t of list) {
        if (["assigned", "in_progress", "blocked", "running"].includes(t.status)) groups.active.push(t);
        else if (t.status === "completed") groups.completed.push(t);
        else groups.other.push(t);
      }
      const renderGroup = (label, items) => {
        if (!items.length) return "";
        return `<div class="task-group-label">${esc(label)}</div>` + items.map(t => {
          const kind = taskStatusKind(t.status);
          const selected = t.task_id === selectedTaskId ? " selected" : "";
          const title = t.title || t.task_id;
          return `<button class="task-item${selected}" data-action="select-task" data-task-id="${esc(t.task_id)}" title="${esc(title)}">
            <span class="task-item-dot" style="background:${dotColorFor(kind)};border-radius:50%;"></span>
            <span class="task-item-name">${esc(title)}</span>
          </button>`;
        }).join("");
      };
      const html = renderGroup("Active", groups.active) + renderGroup("Completed", groups.completed) + renderGroup("Other", groups.other);
      host.innerHTML = html || `<div class="task-group-label">No tasks</div>`;
    }

    const PIPELINE_STEPS = [
      { key: "validate", label: "Brief" },
      { key: "request", label: "Request" },
      { key: "model", label: "Design" },
      { key: "render", label: "Staff" },
      { key: "done", label: "Ready" },
    ];

    function pipelinePhaseIndex(phase) {
      const map = { validate: 0, request: 1, model: 2, draft: 2, render: 3, staff: 3, execute: 3, done: 4, report: 4 };
      return map[phase] != null ? map[phase] : -1;
    }

    function renderPipelineCard() {
      const host = document.getElementById("pipeline-card");
      if (!host) return;
      const prog = (typeof designedTaskProgress !== "undefined" && designedTaskProgress) || pipelineState;
      const state = prog.state || "idle";
      const phase = prog.phase || "idle";
      const activeIdx = pipelinePhaseIndex(phase);
      const running = state === "running" || state === "active";
      const finished = state === "finished";
      const failed = state === "failed";

      if (state === "idle" && activeIdx < 0) {
        host.style.display = "none";
        host.innerHTML = "";
        return;
      }
      host.style.display = "block";

      let bg = "#fff", border = "1px solid #e6e3de", badgeBg = "#f3f1ec", badgeColor = "#6b675f", badgeText = "Idle", iconBg = "#f3f1ec", iconColor = "#86827a";
      if (running) { bg = "#fbf9f4"; border = "1px solid #ecdcb8"; badgeBg = "#f6ecd5"; badgeColor = "#9a6c25"; badgeText = "Running"; iconBg = "#f6ecd5"; iconColor = "#b07d2f"; }
      else if (finished) { bg = "#f6faf7"; border = "1px solid #cbe6d4"; badgeBg = "#e3f1e8"; badgeColor = "#3f7d57"; badgeText = "Complete"; iconBg = "#e3f1e8"; iconColor = "#4a8b63"; }
      else if (failed) { bg = "#fbf4f3"; border = "1px solid #ecccc8"; badgeBg = "#f6e0dd"; badgeColor = "#b3524b"; badgeText = "Failed"; iconBg = "#f6e0dd"; iconColor = "#b3524b"; }

      host.style.background = bg;
      host.style.border = border;

      const title = prog.title || (running ? "Designing your organization" : finished ? "Organization ready" : failed ? "Pipeline failed" : "Pipeline");
      const detail = prog.detail || "";

      const stepsHtml = PIPELINE_STEPS.map((step, i) => {
        let dotBorder = "#ddd9d2", dotInner = "transparent", labelColor = "#a39e95";
        const done = (finished) || (activeIdx > i);
        const isActive = activeIdx === i && (running);
        if (done) { dotBorder = "#4a8b63"; dotInner = "#4a8b63"; labelColor = "#3f7d57"; }
        else if (isActive) { dotBorder = "#b07d2f"; dotInner = "#b07d2f"; labelColor = "#9a6c25"; }
        else if (failed && activeIdx === i) { dotBorder = "#b3524b"; dotInner = "#b3524b"; labelColor = "#b3524b"; }
        const dotStyle = isActive ? "animation:wrPulse 1.4s ease-in-out infinite;" : "";
        const col = `<div class="pipe-step-col">
          <div class="pipe-dot-outer" style="border-color:${dotBorder};${dotStyle}"><span class="pipe-dot-inner" style="background:${dotInner};"></span></div>
          <span class="pipe-step-label" style="color:${labelColor};">${esc(step.label)}</span>
        </div>`;
        if (i === PIPELINE_STEPS.length - 1) return col;
        let lineColor = "#e3e0da";
        let lineStyle = `background:${lineColor};`;
        if (activeIdx > i || finished) { lineStyle = "background:#9fd4b7;"; }
        else if (isActive) { lineStyle = "background:repeating-linear-gradient(90deg,#d9c79c 0 6px,transparent 6px 12px);background-size:14px 2px;animation:wrFlow .7s linear infinite;"; }
        return `<div class="pipe-step-wrap" style="flex:1;display:flex;align-items:center;">${col}<div class="pipe-line" style="${lineStyle}"></div></div>`;
      }).join("");

      const retryHtml = failed
        ? `<button class="pipe-retry" data-action="design-task-config" style="border:1px solid #ecccc8;background:#fff;color:#b3524b;">Retry</button>`
        : "";

      host.innerHTML = `
        <div class="pipe-head">
          <div class="pipe-head-left">
            <div class="pipe-icon" style="background:${iconBg};color:${iconColor};">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3M3.4 3.4l2.1 2.1M10.5 10.5l2.1 2.1M12.6 3.4l-2.1 2.1M5.5 10.5l-2.1 2.1" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
            </div>
            <div>
              <div class="pipe-title">${esc(title)}</div>
              ${detail ? `<div class="pipe-sub">${esc(detail)}</div>` : ""}
            </div>
          </div>
          <div class="pipe-head-right">
            <span class="pipe-badge" style="background:${badgeBg};color:${badgeColor};">${esc(badgeText)}</span>
            ${retryHtml}
          </div>
        </div>
        <div class="pipe-steps">${stepsHtml}</div>`;
    }

    function reportStatusMeta(report) {
      const requiresDecision = report.requires_decision || report.decision_required || (report.kind === "decision");
      if (requiresDecision) return { text: "Needs decision", bg: "#f6ecd5", color: "#9a6c25", dot: "#b07d2f" };
      const status = (report.status || "").toLowerCase();
      if (status.includes("fail") || status.includes("block")) return { text: "Blocked", bg: "#f6e0dd", color: "#b3524b", dot: "#b3524b" };
      return { text: "Informational", bg: "#e3f1e8", color: "#3f7d57", dot: "#4a8b63" };
    }

    function renderHomeReportCard(report) {
      const meta = reportStatusMeta(report);
      const fromId = report.from_agent_id || report.author || "CEO";
      const initials = String(fromId).split(/[\s_-]+/).map(w => w[0]).filter(Boolean).slice(0, 2).join("").toUpperCase() || "AI";
      const title = report.title || report.summary_title || `Report ${report.report_id || ""}`.trim();
      const time = report.created_at ? new Date(report.created_at).toLocaleString() : "";
      const confidence = report.confidence != null ? Number(report.confidence) : null;
      const confPct = confidence != null ? Math.round(confidence <= 1 ? confidence * 100 : confidence) : null;
      const confColor = confPct == null ? "#cfcabf" : (confPct >= 70 ? "#4a8b63" : confPct >= 40 ? "#b07d2f" : "#b3524b");
      const summary = report.message || report.summary || report.body || "";
      const risks = report.risks || [];
      const recommendation = report.recommendation || "";
      const nextActions = report.next_actions || report.next_steps || [];
      const decisionQ = report.decision_question || report.decision || (report.requires_decision ? (report.question || "Approve and proceed?") : "");

      const sevColor = (sev) => {
        const s = String(sev || "").toLowerCase();
        if (s.includes("high") || s.includes("crit")) return { bg: "#f6e0dd", color: "#b3524b" };
        if (s.includes("med")) return { bg: "#f6ecd5", color: "#9a6c25" };
        return { bg: "#eceae5", color: "#76726b" };
      };

      const risksHtml = risks.length ? `
        <div class="wr-slabel">Risks</div>
        <div class="wr-risks">
          ${risks.map(r => {
            const text = typeof r === "string" ? r : (r.description || r.text || r.risk || "");
            const sev = typeof r === "string" ? "" : (r.severity || r.level || "");
            const sc = sevColor(sev);
            return `<div class="wr-risk"><span class="wr-risk-sev" style="background:${sc.bg};color:${sc.color};">${esc((sev || "note").toUpperCase())}</span><span class="wr-risk-text">${esc(text)}</span></div>`;
          }).join("")}
        </div>` : "";

      const recHtml = (recommendation || nextActions.length) ? `
        <div class="wr-rec-grid">
          ${recommendation ? `<div><div class="wr-slabel">Recommendation</div><p class="wr-rec-text">${esc(recommendation)}</p></div>` : "<div></div>"}
          ${nextActions.length ? `<div><div class="wr-slabel">Next actions</div><div class="wr-next-actions">${nextActions.map(a => {
            const t = typeof a === "string" ? a : (a.description || a.text || a.action || "");
            return `<div class="wr-na"><span style="color:#86827a;">→</span><span>${esc(t)}</span></div>`;
          }).join("")}</div></div>` : ""}
        </div>` : "";

      const decisionHtml = decisionQ ? `
        <div class="wr-dec-box">
          <div class="wr-dec-hdr">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style="color:#b07d2f;"><path d="M8 1.5 14.5 13H1.5L8 1.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.2v3M8 11.2v.01" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
            <span class="wr-dec-label">Decision required</span>
          </div>
          <p class="wr-dec-q">${esc(decisionQ)}</p>
          <div class="wr-dec-actions">
            <button class="wr-dec-primary" data-action="report-detail" data-report-id="${esc(report.report_id || "")}">Review &amp; decide</button>
            <button class="wr-dec-alt" data-action="report-detail" data-report-id="${esc(report.report_id || "")}">View details</button>
          </div>
        </div>` : "";

      return `
        <div class="wr-report-card">
          <div class="wr-rh">
            <div class="wr-rh-left">
              <div class="wr-avatar">${esc(initials)}</div>
              <div style="min-width:0;">
                <div class="wr-rtitle">${esc(title)}</div>
                <div class="wr-rmeta">
                  <span>${esc(fromId)}</span>
                  ${time ? `<span class="wr-rmeta-dot"></span><span>${esc(time)}</span>` : ""}
                  ${report.task_id ? `<span class="wr-rmeta-dot"></span><span>${esc(report.task_id)}</span>` : ""}
                </div>
              </div>
            </div>
            <div class="wr-rh-right">
              <span class="wr-sbadge" style="background:${meta.bg};color:${meta.color};"><span class="wr-sbadge-dot" style="background:${meta.dot};"></span>${esc(meta.text)}</span>
              ${confPct != null ? `<div class="wr-conf-row"><span class="wr-conf-lbl">Confidence</span><div class="wr-conf-bar"><div class="wr-conf-fill" style="width:${confPct}%;background:${confColor};"></div></div><span class="wr-conf-val">${confPct}%</span></div>` : ""}
            </div>
          </div>
          <div class="wr-rbody">
            ${summary ? `<div class="wr-slabel">Summary</div><p class="wr-summary">${esc(summary)}</p>` : ""}
            ${risksHtml}
            ${recHtml}
          </div>
          ${decisionHtml}
        </div>`;
    }

    function renderHomeHumanReports(reports) {
      const host = document.getElementById("home-human-reports");
      if (!host) return;
      const list = reports || [];
      const countEl = document.getElementById("reports-count");
      if (countEl) countEl.textContent = String(list.length);
      const fromEl = document.getElementById("reports-from");
      if (fromEl) {
        const names = Array.from(new Set(list.map(r => r.from_agent_id || r.author).filter(Boolean)));
        fromEl.textContent = names.length ? `from ${names.slice(0, 3).join(", ")}${names.length > 3 ? "…" : ""}` : "";
      }
      if (!list.length) {
        host.innerHTML = `
          <div class="no-reports-card">
            <div class="no-reports-icon">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9A1.5 1.5 0 0 1 18.5 16H9l-4 4V5.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
            </div>
            <div class="no-reports-title">No reports yet</div>
            <div class="no-reports-desc">When your workforce reaches a milestone or needs a decision, reports from leadership will appear here.</div>
          </div>`;
        return;
      }
      host.innerHTML = list.map(renderHomeReportCard).join("");
    }

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
      "agent_run_path_registered",
      "agent_run_attempt_started",
      "agent_run_attempt_failed",
      "agent_run_retrying",
      "trace_file_written",
      "runtime_config_updated",
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
    let selectedTaskId = "";
    let currentTaskScope = new Set();
    let visibleNodeCount = 0;
    let dashboardMode = localStorage.getItem("workforceDashboardMode") || "simple";
    let communicationPulses = [];
    let longRfcDemoStatus = { status: "idle", running: false };
    let realLlmBenchmarkStatus = { status: "idle", running: false };
    let claudeSteerDemoStatus = { status: "idle", running: false };
    let designedTaskStatus = { status: "idle", running: false };
    let designedTaskConfig = null;
    let designedTaskProgress = {
      active: false,
      state: "idle",
      phase: "idle",
      title: "No design request running.",
      detail: "Click Design Org/Config to generate a draft organization.",
      startedAt: 0,
      finishedAt: 0,
    };
    let designedTaskProgressTimer = null;
    let runtimeConfig = null;
    let refreshScheduled = false;

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
      const groupedOutput = aggregateOutputItems(liveOutput).slice(-cfg("activity", "global_output_limit", 200));
      document.getElementById("output").innerHTML = groupedOutput.map(renderOutputBlock).join("") || `<div class="muted">No agent output.</div>`;
    }

    function renderOutputBlock(item) {
      const label = `${item.task_id || "-"} ${item.agent_id || "-"} ${item.stream || "output"}`;
      return `<div class="output-block">
        <div><span class="pill">${esc(label)}</span></div>
        <div class="output-text">${esc(item.text || "")}</div>
      </div>`;
    }

    function aggregateOutputItems(items) {
      const groups = [];
      for (const item of items || []) {
        const last = groups[groups.length - 1];
        if (last && sameOutputStream(last, item)) {
          last.text = appendStreamText(last.text || "", item.text || "");
          last.timestamp = item.timestamp || last.timestamp;
          last.event_id = item.event_id || last.event_id;
        } else {
          groups.push({ ...item, text: String(item.text || "") });
        }
      }
      return groups;
    }

    function appendOutputItem(items, item, limit) {
      const last = items[items.length - 1];
      if (last && sameOutputStream(last, item)) {
        last.text = appendStreamText(last.text || "", item.text || "");
        last.timestamp = item.timestamp || last.timestamp;
        last.event_id = item.event_id || last.event_id;
      } else {
        items.push({ ...item, text: String(item.text || "") });
      }
      return items.slice(-limit);
    }

    function sameOutputStream(left, right) {
      return String(left?.agent_id || "") === String(right?.agent_id || "")
        && String(left?.task_id || "") === String(right?.task_id || "")
        && String(left?.run_id || "") === String(right?.run_id || "")
        && String(left?.stream || "output") === String(right?.stream || "output");
    }

    function appendStreamText(current, next) {
      return String(current || "") + String(next || "");
    }

    function renderDemoStatus() {
      renderRunStatus({
        labelId: "long-rfc-demo-status",
        buttonId: "start-long-rfc-demo",
        statusPayload: longRfcDemoStatus,
        resultText: longRfcDemoStatus?.result?.final_status ? ` final=${longRfcDemoStatus.result.final_status}` : "",
      });
      const benchmarkResult = realLlmBenchmarkStatus?.result;
      const overall = (benchmarkResult?.scores || []).find(score => score.name === "overall");
      renderRunStatus({
        labelId: "real-llm-benchmark-status",
        buttonId: "start-real-llm-benchmark",
        statusPayload: realLlmBenchmarkStatus,
        resultText: benchmarkResult?.ok != null ? ` ok=${benchmarkResult.ok} score=${overall ? overall.score : ""}` : "",
      });
      renderRunStatus({
        labelId: "claude-steer-demo-status",
        buttonId: "start-claude-steer-demo",
        statusPayload: claudeSteerDemoStatus,
        resultText: claudeSteerDemoStatus?.result?.root_task_id ? ` root=${claudeSteerDemoStatus.result.root_task_id}` : "",
      });
      const designedResult = designedTaskStatus?.result;
      renderRunStatus({
        labelId: "designed-task-status",
        buttonId: "start-designed-task",
        statusPayload: designedTaskStatus,
        resultText: designedResult?.root_task_id ? ` root=${designedResult.root_task_id}` : designedTaskStatus?.root_task_id ? ` root=${designedTaskStatus.root_task_id}` : "",
      });
      const designButton = document.getElementById("design-task-config");
      if (designButton) designButton.disabled = Boolean(designedTaskStatus?.running);
    }

    function renderRunStatus({labelId, buttonId, statusPayload, resultText}) {
      const label = document.getElementById(labelId);
      const button = document.getElementById(buttonId);
      if (!label || !button) return;
      const status = statusPayload?.status || "idle";
      const runId = statusPayload?.run_id ? ` ${statusPayload.run_id}` : "";
      const error = statusPayload?.error ? ` error=${clip(statusPayload.error, 90)}` : "";
      label.textContent = `${status}${runId}${resultText || ""}${error}`;
      button.disabled = Boolean(statusPayload?.running);
    }

    function startDesignedProgress({ phase, title, detail }) {
      designedTaskProgress = {
        active: true,
        state: "active",
        phase,
        title,
        detail,
        startedAt: Date.now(),
        finishedAt: 0,
      };
      if (designedTaskProgressTimer) window.clearInterval(designedTaskProgressTimer);
      designedTaskProgressTimer = window.setInterval(renderDesignedProgress, 1000);
      renderDesignedProgress();
    }

    function updateDesignedProgress({ phase, title, detail }) {
      designedTaskProgress = {
        ...designedTaskProgress,
        active: true,
        state: "active",
        phase: phase || designedTaskProgress.phase,
        title: title || designedTaskProgress.title,
        detail: detail || designedTaskProgress.detail,
      };
      renderDesignedProgress();
    }

    function finishDesignedProgress({ state = "finished", phase = "done", title, detail }) {
      if (designedTaskProgressTimer) {
        window.clearInterval(designedTaskProgressTimer);
        designedTaskProgressTimer = null;
      }
      designedTaskProgress = {
        ...designedTaskProgress,
        active: false,
        state,
        phase,
        title: title || designedTaskProgress.title,
        detail: detail || designedTaskProgress.detail,
        finishedAt: Date.now(),
      };
      renderDesignedProgress();
    }

    function renderDesignedProgress() {
      const panel = document.getElementById("designed-task-progress");
      if (!panel) return;
      const title = document.getElementById("designed-task-progress-title");
      const detail = document.getElementById("designed-task-progress-detail");
      const elapsed = document.getElementById("designed-task-progress-elapsed");
      const steps = document.getElementById("designed-task-progress-steps");
      panel.classList.toggle("active", Boolean(designedTaskProgress.active));
      panel.classList.toggle("finished", designedTaskProgress.state === "finished");
      panel.classList.toggle("failed", designedTaskProgress.state === "failed");
      if (title) title.textContent = designedTaskProgress.title || "No design request running.";
      if (detail) detail.textContent = designedTaskProgress.detail || "";
      if (elapsed) {
        if (designedTaskProgress.startedAt) {
          const end = designedTaskProgress.active ? Date.now() : (designedTaskProgress.finishedAt || Date.now());
          elapsed.textContent = `${Math.max(0, Math.round((end - designedTaskProgress.startedAt) / 1000))}s elapsed`;
        } else {
          elapsed.textContent = "idle";
        }
      }
      if (steps) steps.innerHTML = renderDesignedProgressSteps(designedTaskProgress.phase);
      // Drive the new homepage submit button + pipeline card
      const submitBtn = document.getElementById("design-task-config");
      const submitLabel = document.getElementById("submit-label");
      const iconArrow = document.getElementById("submit-icon-arrow");
      const iconSpin = document.getElementById("submit-icon-spin");
      const running = Boolean(designedTaskProgress.active);
      if (submitBtn) submitBtn.classList.toggle("running", running);
      if (iconArrow) iconArrow.style.display = running ? "none" : "";
      if (iconSpin) iconSpin.style.display = running ? "" : "none";
      if (submitLabel) {
        submitLabel.textContent = selectedTaskId ? "Send" : (running ? "Designing…" : (designedTaskProgress.state === "finished" ? "Re-design" : "Design Org"));
      }
      renderPipelineCard();
    }

    function renderDesignedProgressSteps(activePhase) {
      const items = [
        ["validate", "validate goal"],
        ["request", "send request"],
        ["model", "wait for model"],
        ["render", "render draft"],
        ["done", "ready"],
      ];
      return items.map(([phase, label]) =>
        `<span class="progress-step ${phase === activePhase ? "active" : ""}">${esc(label)}</span>`
      ).join("");
    }

    function nextPaint() {
      return new Promise(resolve => window.requestAnimationFrame(() => window.setTimeout(resolve, 0)));
    }

    async function refreshDemoStatus() {
      const [longRes, benchmarkRes, claudeSteerRes, designedRes] = await Promise.all([
        fetch("/api/demos/long-rfc/status", { cache: "no-store" }),
        fetch("/api/demos/real-llm-benchmark/status", { cache: "no-store" }),
        fetch("/api/demos/claude-steer/status", { cache: "no-store" }),
        fetch("/api/designed-task/status", { cache: "no-store" }),
      ]);
      longRfcDemoStatus = await longRes.json();
      realLlmBenchmarkStatus = await benchmarkRes.json();
      claudeSteerDemoStatus = await claudeSteerRes.json();
      const serverDesignedTaskStatus = await designedRes.json();
      if (designedTaskStatus?.status !== "designing") {
        designedTaskStatus = serverDesignedTaskStatus;
      }
      if (designedTaskStatus?.root_task_id && !selectedTaskId) {
        selectedTaskId = designedTaskStatus.root_task_id;
        scheduleRefresh(0);
      }
      renderDemoStatus();
    }

    async function startLongRfcDemo() {
      longRfcDemoStatus = { status: "starting", running: true };
      renderDemoStatus();
      const res = await fetch("/api/demos/long-rfc/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      longRfcDemoStatus = await res.json();
      if (!res.ok && !longRfcDemoStatus.error) {
        longRfcDemoStatus.error = `HTTP ${res.status}`;
      }
      renderDemoStatus();
      await refresh();
    }

    async function startRealLlmBenchmark() {
      realLlmBenchmarkStatus = { status: "starting", running: true };
      renderDemoStatus();
      const res = await fetch("/api/demos/real-llm-benchmark/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ judge: "heuristic", use_llm: true, reset: true })
      });
      realLlmBenchmarkStatus = await res.json();
      if (!res.ok && !realLlmBenchmarkStatus.error) {
        realLlmBenchmarkStatus.error = `HTTP ${res.status}`;
      }
      renderDemoStatus();
      await refresh();
    }

    async function startClaudeSteerDemo() {
      claudeSteerDemoStatus = { status: "starting", running: true };
      renderDemoStatus();
      const res = await fetch("/api/demos/claude-steer/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      claudeSteerDemoStatus = await res.json();
      if (!res.ok && !claudeSteerDemoStatus.error) {
        claudeSteerDemoStatus.error = `HTTP ${res.status}`;
      }
      renderDemoStatus();
      await refresh();
    }

    function currentCeoAgentId() {
      const rooted = (orgChart || []).find(node => node && node.id);
      if (rooted?.id) return rooted.id;
      const ceo = (agents || []).find(agent => `${agent.role || ""} ${agent.name || ""}`.toLowerCase().includes("ceo")
        || `${agent.role || ""}`.toLowerCase().includes("chief executive"));
      return ceo?.id || "ceo";
    }

    async function sendTaskCeoMessage() {
      const input = document.getElementById("designed-task-goal");
      const message = (input?.value || "").trim();
      if (!selectedTaskId || !message) {
        return;
      }
      const submitLabel = document.getElementById("submit-label");
      if (submitLabel) submitLabel.textContent = "Sending...";
      const res = await fetch("/api/agents/steer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: currentCeoAgentId(),
          task_id: selectedTaskId,
          message,
          action: "message",
          from_agent_id: "human",
        })
      });
      const payload = await res.json();
      if (!res.ok || !payload.ok) {
        throw new Error(payload.error || payload.message || `HTTP ${res.status}`);
      }
      if (input) {
        input.value = "";
        onGoalInput(input);
      }
      if (submitLabel) submitLabel.textContent = "Send";
      await refresh();
    }

    async function routePrimaryComposerAction() {
      const input = document.getElementById("designed-task-goal");
      const message = (input?.value || "").trim();
      if (selectedTaskId && message) {
        await sendTaskCeoMessage();
        return;
      }
      await designTaskConfig();
    }

    async function designTaskConfig() {
      const goal = document.getElementById("designed-task-goal").value.trim();
      if (!goal) {
        designedTaskStatus = { status: "failed", running: false, error: "Enter a task goal first." };
        finishDesignedProgress({
          state: "failed",
          phase: "validate",
          title: "Design request was not started.",
          detail: "Enter a task goal first.",
        });
        renderDemoStatus();
        return;
      }
      designedTaskStatus = { status: "designing", running: true };
      renderDemoStatus();
      const payload = {
        goal,
        headcount_limit: Number(document.getElementById("designed-task-headcount").value || runtimeConfig?.designed_task?.headcount_limit || 6),
        token_budget: Number(document.getElementById("designed-task-token-budget").value || runtimeConfig?.designed_task?.token_budget || 600000),
        management_model: document.getElementById("designed-task-management-model").value.trim() || runtimeConfig?.designed_task?.management_model || "openai/gpt-oss-120b:free",
        worker_model: document.getElementById("designed-task-worker-model").value.trim() || runtimeConfig?.designed_task?.worker_model || "poolside/laguna-m.1:free",
        use_llm: document.getElementById("designed-task-use-llm").checked,
      };
      startDesignedProgress({
        phase: "request",
        title: "Designing organization/config draft...",
        detail: payload.use_llm
          ? `Request is being sent to org_designer with ${payload.management_model}; free OpenRouter models can take a while.`
          : "Running local heuristic org designer.",
      });
      await nextPaint();
      updateDesignedProgress({
        phase: payload.use_llm ? "model" : "render",
        detail: payload.use_llm
          ? "Waiting for the model response. The server is still working until this changes to ready or failed."
          : "Building the draft organization locally.",
      });
      const res = await fetch("/api/designed-task/design", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        designedTaskStatus = { status: "failed", running: false, error: data.error || `HTTP ${res.status}` };
        finishDesignedProgress({
          state: "failed",
          phase: "model",
          title: "Design failed.",
          detail: data.error || `HTTP ${res.status}`,
        });
        renderDemoStatus();
        return;
      }
      updateDesignedProgress({
        phase: "render",
        title: "Rendering draft organization tree...",
        detail: "Model response received. Parsing config JSON and drawing the hierarchy.",
      });
      designedTaskConfig = data.config;
      document.getElementById("designed-task-config-json").value = JSON.stringify(designedTaskConfig, null, 2);
      designedTaskStatus = { status: "draft_ready", running: false, error: "" };
      renderDraftOrganizationTree();
      finishDesignedProgress({
        state: "finished",
        phase: "done",
        title: "Draft organization is ready.",
        detail: `Generated ${designedTaskConfig?.organization?.agents?.length || 0} agents. Review the tree or JSON, then start the task.`,
      });
      renderDemoStatus();
    }

    async function startDesignedTask() {
      const editor = document.getElementById("designed-task-config-json");
      let config;
      try {
        config = JSON.parse(editor.value || "{}");
      } catch (err) {
        designedTaskStatus = { status: "failed", running: false, error: `Invalid JSON: ${err}` };
        finishDesignedProgress({
          state: "failed",
          phase: "validate",
          title: "Cannot start task.",
          detail: `Invalid JSON: ${err}`,
        });
        renderDemoStatus();
        return;
      }
      designedTaskConfig = config;
      renderDraftOrganizationTree();
      designedTaskStatus = { status: "starting", running: true };
      selectedTaskId = "";
      currentTaskScope = new Set();
      startDesignedProgress({
        phase: "request",
        title: "Starting confirmed task run...",
        detail: "Submitting the confirmed config to the runtime and waiting for the root task id.",
      });
      renderDemoStatus();
      await nextPaint();
      const res = await fetch("/api/designed-task/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      designedTaskStatus = await res.json();
      if (!res.ok && !designedTaskStatus.error) {
        designedTaskStatus.error = `HTTP ${res.status}`;
      }
      if (res.ok && !designedTaskStatus.error) {
        finishDesignedProgress({
          state: "finished",
          phase: "done",
          title: "Task run has started.",
          detail: designedTaskStatus.root_task_id
            ? `Root task: ${designedTaskStatus.root_task_id}. Watch the org chart, task filter, replay, and live output below.`
            : "Runtime accepted the task. Watch the org chart, task filter, replay, and live output below.",
        });
      } else {
        finishDesignedProgress({
          state: "failed",
          phase: "request",
          title: "Task run failed to start.",
          detail: designedTaskStatus.error || `HTTP ${res.status}`,
        });
      }
      renderDemoStatus();
      await refresh();
    }

    function setRuntimeConfigStatus(text) {
      const status = document.getElementById("runtime-config-status");
      if (status) status.textContent = text;
    }

    function setTraceExportStatus(html) {
      const status = document.getElementById("task-trace-export-status");
      if (status) status.innerHTML = html;
    }

    async function loadRuntimeConfig() {
      setRuntimeConfigStatus("loading");
      const res = await fetch("/api/runtime-config", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setRuntimeConfigStatus(data.error || `HTTP ${res.status}`);
        return;
      }
      runtimeConfig = data.config || {};
      document.getElementById("runtime-config-json").value = JSON.stringify(runtimeConfig, null, 2);
      applyRuntimeConfigDefaults();
      setRuntimeConfigStatus(`loaded ${data.path || ""}`.trim());
    }

    async function saveRuntimeConfig() {
      const editor = document.getElementById("runtime-config-json");
      let config;
      try {
        config = JSON.parse(editor.value || "{}");
      } catch (err) {
        setRuntimeConfigStatus(`invalid JSON: ${err}`);
        return;
      }
      setRuntimeConfigStatus("saving");
      const res = await fetch("/api/runtime-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setRuntimeConfigStatus(data.error || `HTTP ${res.status}`);
        return;
      }
      runtimeConfig = data.config || {};
      editor.value = JSON.stringify(runtimeConfig, null, 2);
      applyRuntimeConfigDefaults();
      setRuntimeConfigStatus(`saved ${data.path || ""}`.trim());
      await refresh();
    }

    function applyRuntimeConfigDefaults() {
      const defaults = runtimeConfig?.designed_task || {};
      const setValue = (id, value) => {
        const element = document.getElementById(id);
        if (element && value != null) {
          element.value = value;
        }
      };
      setValue("designed-task-headcount", defaults.headcount_limit);
      setValue("designed-task-token-budget", defaults.token_budget);
      setValue("designed-task-management-model", defaults.management_model);
      setValue("designed-task-worker-model", defaults.worker_model);
      const useLlm = document.getElementById("designed-task-use-llm");
      if (useLlm && defaults.use_llm != null) useLlm.checked = Boolean(defaults.use_llm);
    }

    async function exportSelectedTaskTrace() {
      if (!selectedTaskId) {
        setTraceExportStatus("select a task first");
        return;
      }
      setTraceExportStatus("exporting");
      const res = await fetch("/api/tasks/export-trace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: selectedTaskId, include_descendants: true })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setTraceExportStatus(esc(data.error || `HTTP ${res.status}`));
        return;
      }
      setTraceExportStatus(`<a href="${esc(data.url)}" target="_blank" rel="noreferrer">trace file</a>`);
      await refresh();
    }

    function readDesignedTaskConfigFromEditor() {
      const editor = document.getElementById("designed-task-config-json");
      const text = editor?.value?.trim() || "";
      if (!text) return { config: designedTaskConfig, error: "" };
      try {
        const config = JSON.parse(text);
        designedTaskConfig = config;
        return { config, error: "" };
      } catch (err) {
        return { config: null, error: String(err) };
      }
    }

    function renderDraftOrganizationTree() {
      const treeHost = document.getElementById("draft-org-tree");
      const summary = document.getElementById("draft-org-summary");
      if (!treeHost || !summary) return;
      const { config, error } = readDesignedTaskConfigFromEditor();
      if (error) {
        summary.textContent = "invalid JSON";
        treeHost.innerHTML = `<div class="org-placeholder">Cannot render draft tree: ${esc(error)}</div>`;
        return;
      }
      const organization = config?.organization || {};
      const agents = Array.isArray(organization.agents) ? organization.agents : [];
      if (!agents.length) {
        summary.textContent = "no draft";
        treeHost.innerHTML = `<div class="muted">No draft yet.</div>`;
        return;
      }
      const tree = buildDraftAgentTree(agents);
      const companyName = organization.company?.name || config?.case?.title || "designed org";
      summary.textContent = `${agents.length} agents - ${companyName}`;
      treeHost.innerHTML = `<ul class="draft-tree">${tree.map(node => renderDraftAgentNode(node)).join("")}</ul>`;
    }

    function buildDraftAgentTree(agents) {
      const nodeById = new Map();
      const roots = [];
      for (const agent of agents || []) {
        const id = String(agent.id || agent.name || `agent_${nodeById.size + 1}`);
        nodeById.set(id, { ...agent, id, children: [] });
      }
      for (const node of nodeById.values()) {
        const managerId = node.manager_id == null ? "" : String(node.manager_id);
        const manager = managerId ? nodeById.get(managerId) : null;
        if (manager) {
          manager.children.push(node);
        } else {
          roots.push(node);
        }
      }
      return roots;
    }

    function renderDraftAgentNode(node) {
      const children = node.children || [];
      const role = node.role || "";
      const type = node.worker_type || "";
      const nodeClass = draftAgentClass(node);
      const responsibilities = (node.responsibilities || []).slice(0, 3).join(", ");
      const childrenMarkup = children.length
        ? `<ul class="draft-children">${children.map(renderDraftAgentNode).join("")}</ul>`
        : "";
      return `<li class="draft-node">
        <div class="draft-card ${esc(nodeClass)}">
          <div class="draft-card-head">
            <div>
              <div class="draft-agent-name">${esc(node.name || node.id)}</div>
              <div class="draft-agent-role">${esc(role || "Agent")} ${node.department ? "- " + esc(node.department) : ""}</div>
            </div>
            <span class="draft-badge">${esc(node.id)}</span>
          </div>
          <div class="draft-agent-meta">${esc(type || "worker")} - ${esc(node.model || "no model")}</div>
          ${responsibilities ? `<div class="draft-agent-responsibilities">${esc(responsibilities)}</div>` : ""}
        </div>
        ${childrenMarkup}
      </li>`;
    }

    function draftAgentClass(node) {
      const text = `${node.role || ""} ${node.worker_type || ""} ${node.department || ""}`.toLowerCase();
      if (text.includes("ceo") || text.includes("chief") || text.includes("executive")) return "executive";
      if (text.includes("hr") || text.includes("human resources")) return "hr";
      if (text.includes("manager") || text.includes("lead") || text.includes("vp")) return "manager";
      if (text.includes("worker") || text.includes("analyst") || text.includes("research")) return "worker";
      return "generic";
    }

    function renderOrgChart() {
      renderModeControls();
      visibleNodeCount = 0;
      const agentCount = agents.length || countNodes(orgChart);
      document.body.classList.toggle("mode-simple", dashboardMode === "simple");
      document.body.classList.toggle("mode-debug", dashboardMode === "debug");
      document.getElementById("org-summary").textContent = `${agentCount} agents - ${dashboardMode} mode - collapse depth ${cfg("dashboard", "collapse_depth", 3)}`;
      renderCommunicationPulses();
      document.getElementById("org-chart").innerHTML = orgChart.length
        ? `<ul class="org-tree">${orgChart.map(node => renderOrgNode(node, 0)).join("")}</ul>`
        : `<div class="muted">No agents.</div>`;
    }

    function renderModeControls() {
      document.body.classList.toggle("mode-simple", dashboardMode === "simple");
      document.body.classList.toggle("mode-debug", dashboardMode === "debug");
      const simple = document.getElementById("mode-simple");
      const debug = document.getElementById("mode-debug");
      if (simple) simple.classList.toggle("active", dashboardMode === "simple");
      if (debug) debug.classList.toggle("active", dashboardMode === "debug");
      const slider = document.getElementById("sb-mode-slider");
      if (slider) slider.style.transform = dashboardMode === "debug" ? "translateX(100%)" : "translateX(0)";
      if (simple) simple.style.color = dashboardMode === "simple" ? "#1c1b19" : "#8a867f";
      if (debug) debug.style.color = dashboardMode === "debug" ? "#1c1b19" : "#8a867f";
    }

    function renderCommunicationPulses() {
      const host = document.getElementById("communication-pulses");
      if (!host) return;
      const now = Date.now();
      communicationPulses = communicationPulses.filter(pulse => pulse.expiresAt > now);
      host.innerHTML = communicationPulses.length
        ? communicationPulses.map(pulse => `<div class="communication-pulse ${esc(pulse.kind || "")}">${esc(pulse.text)}</div>`).join("")
        : `<div class="muted">No recent assignments, discussions, or reports.</div>`;
    }

    function appendCommunicationPulse(event) {
      const pulse = communicationPulseFromEvent(event);
      if (!pulse) return;
      communicationPulses.push({
        ...pulse,
        id: event.event_id || `${Date.now()}_${communicationPulses.length}`,
        expiresAt: Date.now() + 4500,
      });
      communicationPulses = communicationPulses.slice(-8);
      renderCommunicationPulses();
      window.setTimeout(renderCommunicationPulses, 4700);
    }

    function communicationPulseFromEvent(event) {
      const payload = event.payload || {};
      const actor = event.actor_id || payload.from_agent_id || "system";
      const tool = payload.tool_name || "";
      if (event.event_type === "discussion_message") {
        const target = payload.to_agent_id || payload.target_agent_id || "peer";
        return { kind: "discuss", text: `${actor} discussed with ${target}: ${clip(payload.message || "", 80)}` };
      }
      if (event.event_type === "report_registered") {
        const target = payload.to_agent_id || "manager";
        return { kind: "report", text: `${actor} reported to ${target}: ${clip(payload.summary || payload.status || "", 80)}` };
      }
      if (event.event_type === "human_report_registered") {
        return { kind: "human", text: `${actor} reported to human: ${clip(payload.title || payload.message || "", 80)}` };
      }
      if (!isToolCallEvent(event.event_type || "")) return null;
      if (tool === "assign") {
        const target = payload.to_agent_id || payload.assigned_to || payload.target_agent_id || "worker";
        const verb = event.event_type.endsWith("_started") ? "assigning" : event.event_type.endsWith("_finished") ? "assigned" : "assign";
        return { kind: "assign", text: `${actor} ${verb} ${target}: ${clip(payload.title || payload.message || payload.task_id || "", 80)}` };
      }
      if (tool === "discuss") {
        const target = payload.to_agent_id || payload.target_agent_id || "peer";
        return { kind: "discuss", text: `${actor} discussing with ${target}: ${clip(payload.message || "", 80)}` };
      }
      if (tool === "report" || tool === "report_to_human") {
        const target = tool === "report_to_human" ? "human" : (payload.to_agent_id || "manager");
        return { kind: tool === "report_to_human" ? "human" : "report", text: `${actor} reporting to ${target}: ${clip(payload.title || payload.message || payload.report_id || "", 80)}` };
      }
      if (tool === "check_progress") {
        const target = payload.to_agent_id || payload.target_agent_id || payload.worker_id || "worker";
        return { kind: "report", text: `${actor} checking progress with ${target}` };
      }
      return null;
    }

    function renderOrgNode(node, depth) {
      const maxVisible = cfg("dashboard", "max_visible_agents", 80);
      if (visibleNodeCount >= maxVisible && depth > 0) {
        const total = 1 + Number(node.descendant_count || 0);
        return `<li class="org-node"><div class="org-placeholder">${esc(node.name)} and ${esc(total)} agent(s) hidden by display limit.</div></li>`;
      }
      visibleNodeCount += 1;
      const activity = ensureAgentActivity(node.id, node.activity);
      const summary = summarizeActivity(activity, node);
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
      if (dashboardMode !== "debug") {
        return renderSimpleOrgNode({ node, depth, summary, active, work, toggle, childrenMarkup });
      }
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
            ${renderActivityBlock("Output", aggregateOutputItems(activity.output), renderOutputItem)}
            ${(activity.errors || []).length ? renderActivityBlock("Errors", aggregateOutputItems(activity.errors), renderErrorItem) : ""}
            ${renderActivityBlock("Tools", activity.tools, renderToolItem)}
            ${renderActivityBlock("Events", activity.events, renderEventItem)}
          </div>
        </div>
        ${childrenMarkup}
      </li>`;
    }

    function renderSimpleOrgNode({ node, summary, active, work, toggle, childrenMarkup }) {
      const status = node.status || "idle";
      return `<li class="org-node">
        <div class="agent-node simple-agent-node ${active ? "active" : ""}" data-agent-id="${esc(node.id)}">
          <div class="simple-agent-main">
            <div class="agent-title">
              ${renderAgentIcon(node.icon)}
              <div>
                <div class="agent-name">${esc(node.name || node.id)}</div>
                <div class="simple-agent-role">${esc(node.role || "Agent")} - ${esc(node.worker_type || "")}</div>
              </div>
            </div>
            <div class="agent-controls">
              ${toggle}
              <button data-action="agent-detail" data-agent-detail="${esc(node.id)}">Open</button>
              <span class="status ${statusClass(status)}">${esc(status)}</span>
            </div>
          </div>
          <div class="simple-agent-summary ${active ? "active" : ""}">
            <span class="summary-dot"></span>
            <span class="summary-text">${esc(summary.text || "Idle.")}</span>
          </div>
          <div class="simple-agent-work">tasks: ${esc(work)}</div>
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
      const label = item.stream === "error" ? "Error" : (item.stream || "output");
      const cls = item.stream === "error" ? " activity-error" : "";
      return `<div class="output-block${cls}">
        <div><span class="pill">${esc(label)}</span></div>
        <div class="output-text">${esc(item.text || "")}</div>
      </div>`;
    }

    function renderErrorItem(item) {
      return `<div class="activity-item activity-error">Error: ${esc(item.text || "")}</div>`;
    }

    function renderHumanReports(reports) {
      const host = document.getElementById("human-reports");
      if (!host) return;
      const items = (reports || []).slice().reverse();
      host.innerHTML = items.length
        ? items.map(renderHumanReportCard).join("")
        : `<div class="muted">No CEO report to human yet.</div>`;
    }

    function renderHumanReportCard(report) {
      const confidence = report.confidence == null ? "" : ` confidence=${Number(report.confidence).toFixed(2)}`;
      const status = report.status ? ` status=${report.status}` : "";
      const decision = report.requires_decision ? `<span class="pill">human decision needed</span>` : `<span class="pill">for human</span>`;
      const nextAction = report.next_action
        ? `<div class="human-report-next">Next action: ${esc(report.next_action)}</div>`
        : "";
      return `<div class="human-report-card ${report.requires_decision ? "requires-decision" : ""}">
        <div class="human-report-head">
          <div>
            <div class="human-report-title">${esc(report.title || "CEO report to human")}</div>
            <div class="human-report-meta">from ${esc(report.from_agent_id || "-")} task=${esc(report.task_id || "-")}${esc(status)}${esc(confidence)}</div>
          </div>
          ${decision}
        </div>
        <div class="human-report-message">${esc(report.message || "")}</div>
        ${nextAction}
      </div>`;
    }

    function renderManagerReports(reports) {
      const host = document.getElementById("manager-reports");
      if (!host) return;
      const items = (reports || []).slice().reverse();
      host.innerHTML = items.length
        ? items.map(renderManagerReportCard).join("")
        : `<div class="muted">No internal manager report yet.</div>`;
    }

    function renderManagerReportCard(report) {
      const confidence = report.confidence == null ? "" : ` confidence=${Number(report.confidence).toFixed(2)}`;
      const decision = report.requires_decision ? `<span class="pill">manager decision needed</span>` : `<span class="pill">${esc(report.status || "report")}</span>`;
      const workDone = (report.work_done || []).length
        ? `<div class="manager-report-next">Work done: ${esc((report.work_done || []).join("; "))}</div>`
        : "";
      const evidence = renderReportEvidence(report.evidence || []);
      const nextAction = report.next_action
        ? `<div class="manager-report-next">Next action: ${esc(report.next_action)}</div>`
        : "";
      return `<div class="manager-report-card">
        <div class="manager-report-head">
          <div>
            <div class="manager-report-title">${esc(report.report_id || "manager report")}</div>
            <div class="manager-report-meta">${esc(report.from_agent_id || "-")} -> ${esc(report.to_agent_id || "-")} task=${esc(report.task_id || "-")}${esc(confidence)}</div>
          </div>
          ${decision}
        </div>
        <div class="manager-report-summary">${esc(report.summary || "")}</div>
        ${workDone}
        ${evidence}
        ${nextAction}
      </div>`;
    }

    function renderReportEvidence(evidenceItems) {
      const evidence = (evidenceItems || []).slice(0, 4).map(item => {
        const type = item?.type || "evidence";
        const path = item?.path || "";
        if (path) return `${esc(type)} ${renderFileLink(path, "file")}`;
        return esc(type);
      }).join(" ");
      return evidence ? `<div class="manager-report-evidence">Evidence: ${evidence}</div>` : "";
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
        agentActivity[agentId] = fallback || { output: [], full_output: [], errors: [], tools: [], events: [] };
      }
      if (!agentActivity[agentId].output) agentActivity[agentId].output = [];
      if (!agentActivity[agentId].full_output) agentActivity[agentId].full_output = [...agentActivity[agentId].output];
      if (!agentActivity[agentId].errors) agentActivity[agentId].errors = [];
      if (!agentActivity[agentId].tools) agentActivity[agentId].tools = [];
      if (!agentActivity[agentId].events) agentActivity[agentId].events = [];
      return agentActivity[agentId];
    }

    function summarizeActivity(activity, node = null) {
      const candidates = [];
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
      if (node?.status === "busy" || node?.status === "blocked" || node?.status === "assigned" || node?.status === "in_progress") {
        return {mode: "local", text: `Status: ${node.status}`, active: true};
      }
      return {mode: "local", text: "Idle.", active: false};
    }

    function compactEventDetail(event) {
      const payload = event.payload || {};
      const keys = ["tool_name", "requested_tool_name", "assigned_to", "to_agent_id", "target_agent_id", "profile_agent_id", "status", "stream", "returncode", "timed_out", "report_id", "human_report_id", "decision", "title", "message", "url", "trace_path", "run_dir", "prompt_path", "response_path", "raw_response_path", "error_path", "attempt", "max_attempts", "next_attempt", "delay_seconds", "doc_id", "request_id", "revision"];
      return keys.filter(key => payload[key] != null).map(key => `${key}=${clip(payload[key], 120)}`).join(" ");
    }

    function appendAgentEvent(event) {
      if (selectedTaskId && !eventMatchesCurrentTaskFilter(event)) return;
      if (!event.actor_id) return;
      appendCommunicationPulse(event);
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
        liveOutput = appendOutputItem(liveOutput, item, cfg("activity", "global_output_limit", 200));
        if (item.stream === "error") {
          activity.errors = appendOutputItem(activity.errors, item, cfg("activity", "recent_output_items", 12));
        } else {
          activity.output = appendOutputItem(activity.output, item, cfg("activity", "recent_output_items", 12));
        }
        activity.full_output = appendOutputItem(activity.full_output, item, cfg("activity", "full_stream_limit", 200));
      } else if (isToolCallEvent(event.event_type || "")) {
        const status = (event.event_type || "").startsWith("mcp_tool_call_")
          ? (event.event_type || "").replace("mcp_tool_call_", "")
          : (event.event_type || "").replace("tool_call_", "");
        activity.tools.push({
          event_id: event.event_id,
          timestamp: event.timestamp,
          event_type: event.event_type,
          task_id: event.task_id,
          agent_id: event.actor_id,
          tool_name: event.payload?.tool_name,
          status,
          target_agent_id: event.payload?.target_agent_id || event.payload?.to_agent_id || event.payload?.assigned_to || event.payload?.worker_id,
          message: event.payload?.message || event.payload?.title || event.payload?.error || event.payload?.url || "",
          result_id: event.payload?.task_id || event.payload?.report_id || event.payload?.human_report_id || event.payload?.event_id || "",
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

    function eventMatchesCurrentTaskFilter(event) {
      if (!selectedTaskId) return true;
      if (!currentTaskScope.size) return false;
      if (event.task_id && currentTaskScope.has(event.task_id)) return true;
      const payload = event.payload || {};
      for (const key of ["task_id", "parent_task_id", "root_goal_id", "root_task_id", "final_task_id", "reviewed_task_id"]) {
        if (payload[key] && currentTaskScope.has(String(payload[key]))) return true;
      }
      for (const key of ["task_ids", "current_task_ids"]) {
        if (Array.isArray(payload[key]) && payload[key].some(value => currentTaskScope.has(String(value)))) return true;
      }
      return false;
    }

    function isToolCallEvent(eventType) {
      return eventType.startsWith("mcp_tool_call_") || eventType.startsWith("tool_call_");
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
      const profile = node.personal_profile || {};
      const work = (node.current_task_ids || []).join(", ") || "-";
      const modelLimits = renderModelLimit(node.model_capabilities);
      const systemPrompt = node.system_prompt || "No system prompt stored for this agent.";
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
            <h3>Personal Profile</h3>
            <div class="activity-item">summary: ${esc(profile.summary || "No profile summary yet.")}</div>
            <div class="activity-item">specialty tags: ${esc((profile.specialty_tags || []).join(", ") || "-")}</div>
            <div class="activity-item">can do: ${esc((profile.can_do || []).slice(-6).join("; ") || "-")}</div>
            <div class="activity-item">knows about: ${esc((profile.knows_about || []).slice(-6).join("; ") || "-")}</div>
            <div class="activity-item">experiences: ${esc((profile.experiences || []).length || 0)} - revision ${esc(profile.revision || "-")}</div>
          </div>
          <div class="detail-section">
            <h3>Model And Prompt</h3>
            <div class="activity-item">model: ${esc(node.model || "runtime default")}</div>
            <div class="activity-item">${esc(modelLimits)}</div>
            <pre>${esc(systemPrompt)}</pre>
          </div>
          <div class="detail-section">
            <h3>Full Stream</h3>
            <div class="stream-box">${aggregateOutputItems(activity.full_output || activity.output || []).map(renderOutputItem).join("") || `<div class="muted output-line">No stream output.</div>`}</div>
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
        scheduleRefresh(250);
      });
      eventSource.addEventListener("heartbeat", (message) => {
        const item = JSON.parse(message.data);
        eventCursor = Math.max(eventCursor, item.cursor || 0);
        if (longRfcDemoStatus?.running || realLlmBenchmarkStatus?.running || claudeSteerDemoStatus?.running || designedTaskStatus?.running) {
          refreshDemoStatus().catch(err => console.error(err));
        }
      });
      eventSource.onerror = () => {
        document.getElementById("stream-status").textContent = "reconnecting";
      };
    }

    function scheduleRefresh(delay = 250) {
      if (refreshScheduled) return;
      refreshScheduled = true;
      window.setTimeout(() => {
        refreshScheduled = false;
        refresh().catch(err => console.error(err));
      }, delay);
    }

    async function refresh() {
      const stateUrl = selectedTaskId ? `/api/state?task_id=${encodeURIComponent(selectedTaskId)}` : "/api/state";
      const res = await fetch(stateUrl, { cache: "no-store" });
      const data = await res.json();
      dashboardConfig = deepMerge(DEFAULT_CONFIG, data.config || {});
      eventCursor = Math.max(eventCursor, data.cursor || 0);
      liveOutput = data.agent_output || data.worker_output || [];
      orgChart = data.org_chart || [];
      agents = data.agents || [];
      agentActivity = data.agent_activity || {};
      currentTaskScope = new Set(data.task_filter?.task_ids || []);
      renderTaskFilterOptions(data.all_tasks || data.tasks || []);
      document.getElementById("mission").textContent = `${data.company.name} - ${data.company.mission || "No mission"}`;
      document.getElementById("updated").textContent = new Date().toLocaleTimeString();
      const active = data.tasks.filter(t => ["assigned", "in_progress", "blocked"].includes(t.status)).length;
      const completed = data.tasks.filter(t => t.status === "completed").length;
      const failed = data.tasks.filter(t => t.status === "failed").length;
      const traceLinks = (data.trace_files || []).slice(-2).map(file => renderFileLink(file.path, file.label || file.run_id || "trace")).join(" ") || "-";
      document.getElementById("metrics").innerHTML = [
        ["Agents", `${data.agents.length}${data.budget.headcount_limit ? " / " + data.budget.headcount_limit : ""}`],
        ["Active Tasks", active],
        ["Completed", completed],
        ["Failed", failed],
        ["Tokens", `${data.budget.tokens_used} / ${data.budget.token_budget_limit}`],
        ["Trace Files", traceLinks],
        ["Output Events", liveOutput.length],
      ].map(([label, value]) => `<div class="panel span-3"><h2>${esc(label)}</h2><div class="metric">${value}</div></div>`).join("");
      document.getElementById("agents").innerHTML = rows(["Agent", "Role", "Status", "Model", "Current Work"], data.agents.map(a => [
        `<button data-action="agent-detail" data-agent-detail="${esc(a.id)}">${esc(a.name)}</button>`,
        esc(a.role),
        `<span class="status ${statusClass(a.status)}">${esc(a.status)}</span>`,
        esc(a.model || "-"),
        esc((a.current_task_ids || []).join(", ") || "-")
      ]));
      renderOrgChart();
      renderStatusBar(data);
      renderSidebarTasks(data.all_tasks || data.tasks || [], data.tasks || []);
      renderPipelineCard();
      renderHomeHumanReports(data.human_reports || []);
      renderHumanReports(data.human_reports || []);
      renderManagerReports(data.reports || []);
      document.getElementById("tasks").innerHTML = rows(["Task", "Title", "Status", "Assignee"], data.tasks.map(t => [
        esc(t.task_id),
        esc(t.title),
        `<span class="status ${statusClass(t.status)}">${esc(t.status)}</span>`,
        esc(t.assigned_to || "-")
      ]));
      document.getElementById("runs").innerHTML = rows(["Run", "Task", "Agent", "Kind", "Status", "Runtime", "Files"], (data.agent_runs || data.worker_runs).map(r => [
        esc(r.run_id),
        esc(r.task_id || "-"),
        esc(r.agent_id),
        esc(r.kind || "worker"),
        `<span class="status ${statusClass(r.status)}">${esc(r.status)}${r.returncode != null ? " " + esc(r.returncode) : ""}</span>`,
        esc(r.executable || r.adapter || r.model || "-"),
        [
          r.prompt_path ? renderFileLink(r.prompt_path, "prompt") : "",
          r.raw_response_path ? renderFileLink(r.raw_response_path, "raw") : "",
          r.response_path ? renderFileLink(r.response_path, "response") : "",
          r.error_path ? renderFileLink(r.error_path, "error") : "",
          r.last_attempt_error_path ? renderFileLink(r.last_attempt_error_path, "attempt-error") : "",
          r.stdout_path ? renderFileLink(r.stdout_path, "stdout") : "",
          r.stderr_path ? renderFileLink(r.stderr_path, "stderr") : ""
        ].filter(Boolean).join(" ") || "-"
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
      await refreshDemoStatus();
      document.getElementById("replay").textContent = data.event_replay;
      document.getElementById("trajectories").textContent = data.trajectories;
      connectStream();
    }

    function renderTaskFilterOptions(tasks) {
      const select = document.getElementById("task-filter-select");
      if (!select) return;
      const current = selectedTaskId;
      select.innerHTML = `<option value="">All tasks</option>` + (tasks || []).map(task => {
        const label = `${task.task_id} - ${task.title || ""}`.slice(0, 140);
        return `<option value="${esc(task.task_id)}">${esc(label)}</option>`;
      }).join("");
      select.value = current;
    }

    function renderFileLink(path, label = "file") {
      if (!path) return "-";
      return `<a href="/api/file?path=${encodeURIComponent(path)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
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
      if (action === "set-dashboard-mode") {
        dashboardMode = target.dataset.mode === "debug" ? "debug" : "simple";
        localStorage.setItem("workforceDashboardMode", dashboardMode);
        renderModeControls();
        renderOrgChart();
      }
      if (action === "toggle-sidebar") {
        sidebarCollapsed = !sidebarCollapsed;
        const sb = document.getElementById("sidebar");
        if (sb) sb.classList.toggle("collapsed", sidebarCollapsed);
        localStorage.setItem("workforceSidebarCollapsed", sidebarCollapsed ? "1" : "0");
      }
      if (action === "new-task") {
        selectedTaskId = "";
        selectedAgentId = null;
        currentTaskScope = new Set();
        const goal = document.getElementById("designed-task-goal");
        if (goal) { goal.value = ""; onGoalInput(goal); goal.focus(); }
        renderDesignedProgress();
        const scroll = document.getElementById("main-scroll");
        if (scroll) scroll.scrollTo({ top: 0, behavior: "smooth" });
        renderSidebarTasks(lastAllTasks, lastTasks);
        refresh().catch(err => console.error(err));
      }
      if (action === "select-task") {
        selectedTaskId = target.dataset.taskId || "";
        selectedAgentId = null;
        currentTaskScope = new Set();
        renderSidebarTasks(lastAllTasks, lastTasks);
        renderDesignedProgress();
        refresh().catch(err => console.error(err));
      }
      if (action === "report-detail") {
        const scroll = document.getElementById("main-scroll");
        const section = document.getElementById("home-reports-section");
        if (scroll && section) scroll.scrollTo({ top: section.offsetTop - 20, behavior: "smooth" });
      }
      if (action === "start-long-rfc-demo") {
        startLongRfcDemo().catch(err => {
          longRfcDemoStatus = { status: "failed", running: false, error: String(err) };
          renderDemoStatus();
        });
      }
      if (action === "start-real-llm-benchmark") {
        startRealLlmBenchmark().catch(err => {
          realLlmBenchmarkStatus = { status: "failed", running: false, error: String(err) };
          renderDemoStatus();
        });
      }
      if (action === "start-claude-steer-demo") {
        startClaudeSteerDemo().catch(err => {
          claudeSteerDemoStatus = { status: "failed", running: false, error: String(err) };
          renderDemoStatus();
        });
      }
      if (action === "design-task-config") {
        routePrimaryComposerAction().catch(err => {
          designedTaskStatus = { status: "failed", running: false, error: String(err) };
          finishDesignedProgress({
            state: "failed",
            phase: "model",
            title: selectedTaskId ? "Message failed." : "Design failed.",
            detail: String(err),
          });
          renderDemoStatus();
        });
      }
      if (action === "start-designed-task") {
        startDesignedTask().catch(err => {
          designedTaskStatus = { status: "failed", running: false, error: String(err) };
          finishDesignedProgress({
            state: "failed",
            phase: "request",
            title: "Task run failed.",
            detail: String(err),
          });
          renderDemoStatus();
        });
      }
      if (action === "load-runtime-config") {
        loadRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
      }
      if (action === "save-runtime-config") {
        saveRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
      }
      if (action === "export-task-trace") {
        exportSelectedTaskTrace().catch(err => setTraceExportStatus(esc(String(err))));
      }
    });

    document.addEventListener("change", (event) => {
      if (event.target?.id === "task-filter-select") {
        selectedTaskId = event.target.value || "";
        selectedAgentId = null;
        currentTaskScope = new Set();
        refresh().catch(err => console.error(err));
      }
    });

    document.addEventListener("input", (event) => {
      if (event.target?.id === "designed-task-config-json") {
        renderDraftOrganizationTree();
      }
    });

    sidebarCollapsed = localStorage.getItem("workforceSidebarCollapsed") === "1";
    const sbInit = document.getElementById("sidebar");
    if (sbInit) sbInit.classList.toggle("collapsed", sidebarCollapsed);
    renderDraftOrganizationTree();
    renderModeControls();
    renderDesignedProgress();
    renderPipelineCard();
    loadRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
    refresh().catch(err => console.error(err));
  </script>
</body>
</html>
"""
