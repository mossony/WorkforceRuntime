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
        agents = [agent for agent in all_agents if agent.id in agent_ids]
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
        use_llm = bool(payload.get("use_llm", designed_defaults.get("use_llm", True)))
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
    .demo-panel {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .demo-copy {
      min-width: 260px;
      max-width: 760px;
    }
    .demo-title {
      font-weight: 700;
      margin-bottom: 4px;
    }
    .demo-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .task-designer {
      display: grid;
      gap: 10px;
    }
    .simple-home {
      min-height: min(560px, calc(100vh - 170px));
      display: grid;
      align-content: center;
      border: 0;
      background: transparent;
      padding: clamp(24px, 5vh, 72px) 12px 24px;
    }
    .simple-home-inner {
      width: min(980px, 100%);
      margin: 0 auto;
      display: grid;
      gap: 18px;
    }
    .simple-system-line {
      display: flex;
      justify-content: center;
      gap: 8px;
      flex-wrap: wrap;
      min-height: 28px;
    }
    .simple-prompt-title {
      text-align: center;
      font-size: 28px;
      font-weight: 620;
      line-height: 1.2;
      color: #111827;
      text-wrap: balance;
    }
    .simple-composer-shell {
      border: 1px solid #d8dde5;
      border-radius: 8px;
      background: #ffffff;
      box-shadow: 0 18px 54px rgba(15, 23, 42, 0.12);
      overflow: hidden;
    }
    .simple-composer-main {
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr) auto;
      align-items: end;
      gap: 8px;
      padding: 10px;
    }
    .icon-button {
      width: 36px;
      height: 36px;
      border-radius: 999px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      line-height: 1;
    }
    #simple-task-goal {
      min-height: 44px;
      max-height: 180px;
      border: 0;
      padding: 9px 4px;
      resize: vertical;
      font-family: inherit;
      font-size: 16px;
      line-height: 1.45;
      outline: none;
    }
    #simple-task-goal::placeholder {
      color: #64748b;
      opacity: 1;
    }
    .simple-composer-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .simple-composer-actions button {
      min-height: 36px;
      white-space: nowrap;
    }
    .simple-run-button[hidden] { display: none; }
    .simple-config-summary-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      border-top: 1px solid #eef1f5;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .simple-config-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .simple-config-panel {
      display: none;
      border-top: 1px solid #eef1f5;
      padding: 12px;
      background: #fbfcfd;
    }
    .simple-config-panel.open { display: grid; gap: 12px; }
    .simple-config-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 10px;
      align-items: end;
    }
    .simple-config-editors {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .simple-config-editors textarea {
      min-height: 220px;
    }
    .simple-draft-shell {
      display: none;
      width: min(1180px, 100%);
      margin: 8px auto 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 12px;
      overflow-x: auto;
    }
    .simple-draft-shell.has-draft { display: block; }
    .simple-task-report {
      width: min(980px, 100%);
      margin: 0 auto;
      display: grid;
      gap: 8px;
    }
    .simple-task-report-card {
      border: 1px solid #86bdb7;
      border-radius: 8px;
      background: #f3fbfa;
      padding: 12px;
      display: grid;
      gap: 8px;
    }
    .simple-task-report-text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--text);
    }
    .operation-progress {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
      display: grid;
      gap: 9px;
    }
    .operation-progress.active {
      border-color: #94d2c9;
      background: #fcfffe;
    }
    .operation-progress-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
      flex-wrap: wrap;
    }
    .operation-progress-title {
      font-weight: 700;
    }
    .operation-progress-detail {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    .progress-track {
      height: 7px;
      overflow: hidden;
      border-radius: 999px;
      background: #e2e8f0;
    }
    .progress-fill {
      width: 32%;
      height: 100%;
      border-radius: inherit;
      background: var(--accent);
      transform: translateX(-105%);
    }
    .operation-progress.active .progress-fill {
      animation: progress-slide 1.25s ease-in-out infinite;
    }
    .operation-progress.finished .progress-fill {
      width: 100%;
      transform: translateX(0);
      animation: none;
      background: var(--good);
    }
    .operation-progress.failed .progress-fill {
      width: 100%;
      transform: translateX(0);
      animation: none;
      background: var(--bad);
    }
    .progress-steps {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .progress-step {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      background: #ffffff;
    }
    .progress-step.active {
      color: var(--accent);
      border-color: #94d2c9;
      background: var(--accent-weak);
      font-weight: 700;
    }
    @keyframes progress-slide {
      0% { transform: translateX(-105%); }
      55% { transform: translateX(120%); }
      100% { transform: translateX(120%); }
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      align-items: end;
    }
    label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 7px 9px;
      color: var(--text);
      background: #fff;
      font: inherit;
    }
    textarea {
      min-height: 86px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }
    .config-editor {
      min-height: 260px;
    }
    .draft-org-panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
      display: grid;
      gap: 10px;
    }
    .draft-org-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .draft-org-title {
      font-weight: 700;
    }
    .draft-tree, .draft-children {
      list-style: none;
      padding-left: 0;
      margin: 0;
    }
    .draft-children {
      margin-left: 26px;
      padding-left: 18px;
      border-left: 2px solid var(--line);
    }
    .draft-node {
      position: relative;
      margin: 10px 0;
    }
    .draft-children > .draft-node::before {
      content: "";
      position: absolute;
      left: -18px;
      top: 20px;
      width: 16px;
      border-top: 2px solid var(--line);
    }
    .draft-card {
      display: grid;
      gap: 5px;
      max-width: 560px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 9px 10px;
    }
    .draft-card.executive {
      border-color: #86bdb7;
      background: #f3fbfa;
    }
    .draft-card.manager {
      border-color: #9bb9ea;
      background: #f5f8fe;
    }
    .draft-card.worker {
      border-color: #c4b5fd;
      background: #faf7ff;
    }
    .draft-card.hr {
      border-color: #f0c27b;
      background: #fff9ed;
    }
    .draft-card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
    }
    .draft-agent-name {
      font-weight: 700;
      color: var(--text);
    }
    .draft-agent-role, .draft-agent-meta, .draft-agent-responsibilities {
      color: var(--muted);
      font-size: 12px;
    }
    .simple-draft-shell .draft-tree {
      display: flex;
      justify-content: center;
      min-width: max-content;
      padding: 8px 12px 0;
    }
    .simple-draft-shell .draft-node {
      position: relative;
      margin: 0;
      padding: 0 10px 30px;
      text-align: center;
    }
    .simple-draft-shell .draft-card {
      width: 240px;
      text-align: left;
      margin: 0 auto;
      position: relative;
    }
    .simple-draft-shell .draft-children {
      position: relative;
      display: flex;
      justify-content: center;
      gap: 4px;
      margin-left: 0;
      padding: 34px 0 0;
      border-left: 0;
    }
    .simple-draft-shell .draft-children::before {
      content: "";
      position: absolute;
      top: 16px;
      left: 24px;
      right: 24px;
      border-top: 1px solid #c8d1dc;
    }
    .simple-draft-shell .draft-children > .draft-node::before {
      content: "";
      position: absolute;
      top: 16px;
      left: 50%;
      height: 18px;
      border-left: 1px solid #c8d1dc;
    }
    .simple-draft-shell .draft-node:has(> .draft-children) > .draft-card::after {
      content: "";
      position: absolute;
      left: 50%;
      bottom: -31px;
      height: 30px;
      border-left: 1px solid #c8d1dc;
    }
    .draft-badge {
      flex: 0 0 auto;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 11px;
      color: var(--muted);
      background: #f8fafc;
    }
    .human-reports {
      display: grid;
      gap: 10px;
    }
    .human-report-card {
      border: 1px solid #86bdb7;
      border-radius: 8px;
      background: #f3fbfa;
      padding: 11px 12px;
      display: grid;
      gap: 8px;
    }
    .human-report-card.requires-decision {
      border-color: #f0c27b;
      background: #fff9ed;
    }
    .human-report-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .human-report-title {
      font-weight: 800;
    }
    .human-report-meta, .human-report-next {
      color: var(--muted);
      font-size: 12px;
    }
    .human-report-message {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--text);
    }
    .manager-reports {
      display: grid;
      gap: 10px;
    }
    .manager-report-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 11px 12px;
      display: grid;
      gap: 8px;
    }
    .manager-report-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .manager-report-title {
      font-weight: 800;
    }
    .manager-report-meta, .manager-report-next, .manager-report-evidence {
      color: var(--muted);
      font-size: 12px;
    }
    .manager-report-summary {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--text);
    }
    .primary-button {
      background: var(--accent);
      color: #ffffff;
      border-color: var(--accent);
      font-weight: 700;
    }
    .primary-button:hover {
      background: #0b5f59;
      color: #ffffff;
    }
    .primary-button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
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
    .output-block {
      border-bottom: 1px solid var(--line);
      padding: 8px 0;
      display: grid;
      gap: 6px;
    }
    .output-text {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      color: #263447;
    }
    .pill {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      color: var(--muted);
      font-size: 12px;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .mode-toggle {
      display: inline-flex;
      gap: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 3px;
      background: #ffffff;
    }
    .mode-toggle button {
      border: 0;
      border-radius: 6px;
      padding: 5px 10px;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
    }
    .mode-toggle button.active {
      background: #12312f;
      color: #ffffff;
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
    body.mode-simple .org-toolbar {
      width: min(1180px, 100%);
      margin: 0 auto 12px;
    }
    body.mode-simple #org-chart {
      overflow-x: auto;
      padding: 12px 0 4px;
    }
    body.mode-simple .org-tree {
      display: flex;
      justify-content: center;
      min-width: max-content;
      padding: 8px 12px 0;
    }
    body.mode-simple .org-node {
      position: relative;
      margin: 0;
      padding: 0 10px 30px;
      text-align: center;
    }
    body.mode-simple .org-children {
      position: relative;
      display: flex;
      justify-content: center;
      gap: 4px;
      margin-left: 0;
      padding: 34px 0 0;
      border-left: 0;
    }
    body.mode-simple .org-children::before {
      content: "";
      position: absolute;
      top: 16px;
      left: 24px;
      right: 24px;
      border-top: 1px solid #c8d1dc;
    }
    body.mode-simple .org-children > .org-node::before {
      content: "";
      position: absolute;
      top: 16px;
      left: 50%;
      height: 18px;
      border-left: 1px solid #c8d1dc;
    }
    body.mode-simple .simple-agent-node.has-children::after {
      content: "";
      position: absolute;
      left: 50%;
      bottom: -31px;
      height: 30px;
      border-left: 1px solid #c8d1dc;
    }
    body.mode-simple .simple-overflow-node {
      min-width: 190px;
      display: flex;
      align-items: flex-start;
      justify-content: center;
    }
    body.mode-simple .simple-overflow-node .org-placeholder {
      margin-top: 3px;
      min-width: 170px;
      text-align: center;
      background: #ffffff;
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
    .simple-agent-node {
      border-color: #d8e1ea;
      background: #ffffff;
      box-shadow: 0 1px 0 rgba(15, 23, 42, 0.03);
      width: 260px;
      min-height: 134px;
      text-align: left;
      position: relative;
      transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
      cursor: pointer;
    }
    .simple-agent-node.active {
      border-color: #66b7ad;
      background: #f4fbfa;
    }
    .simple-agent-node:hover {
      border-color: #94a3b8;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
      transform: translateY(-1px);
    }
    .simple-agent-main {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
    }
    .simple-agent-role {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    .simple-agent-work {
      color: #334155;
      font-size: 12px;
      white-space: nowrap;
    }
    .simple-agent-summary {
      border: 1px solid #d8e6e3;
      border-radius: 8px;
      background: #f8fbfb;
      padding: 8px 9px;
      display: flex;
      gap: 8px;
      align-items: center;
      min-width: 0;
    }
    .simple-agent-summary .summary-text {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .communication-pulses {
      min-height: 34px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin: 0 0 10px;
    }
    .communication-pulse {
      border: 1px solid #84b9b3;
      border-radius: 8px;
      background: #f1fbfa;
      color: #12312f;
      padding: 7px 9px;
      font-size: 12px;
      box-shadow: 0 4px 12px rgba(17, 94, 89, 0.12);
      animation: pulse-fade 4.5s ease forwards;
    }
    .communication-pulse.report {
      border-color: #9fbce4;
      background: #f4f8ff;
      color: #1e3a5f;
    }
    .communication-pulse.human {
      border-color: #deb36b;
      background: #fff9ed;
      color: #6d4a11;
    }
    @keyframes pulse-fade {
      0% { transform: translateY(6px); opacity: 0; }
      12% { transform: translateY(0); opacity: 1; }
      82% { transform: translateY(0); opacity: 1; }
      100% { transform: translateY(-4px); opacity: 0; }
    }
    body.mode-simple .debug-only { display: none; }
    body.mode-debug .simple-only { display: none; }
    body.mode-simple [data-module] { display: none; }
    body.mode-simple [data-module].module-active { display: block; }
    body.mode-debug [data-module] { display: block; }
    body.mode-simple main {
      gap: 16px;
      padding: 14px clamp(12px, 3vw, 28px) 40px;
    }
    body.mode-simple .module-toggle {
      display: none;
    }
    body.mode-simple #metrics {
      width: min(980px, 100%);
      margin: 0 auto;
      display: flex;
      gap: 8px;
      justify-content: center;
      flex-wrap: wrap;
    }
    body.mode-simple #metrics .panel {
      flex: 0 1 auto;
      padding: 5px 9px;
      border-radius: 999px;
      background: #ffffff;
      box-shadow: none;
    }
    body.mode-simple #metrics h2 {
      display: inline;
      text-transform: none;
      font-size: 12px;
      margin-right: 5px;
    }
    body.mode-simple #metrics .metric {
      display: inline;
      font-size: 12px;
      font-weight: 700;
      margin: 0;
    }
    .module-toggle {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .module-toggle button.active {
      background: #111827;
      color: #fff;
      border-color: #111827;
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
    .summary-text-expanded {
      overflow: visible;
      text-overflow: initial;
      white-space: normal;
      line-height: 1.45;
    }
    .summary-actions {
      margin-top: 6px;
      display: flex;
      justify-content: flex-end;
    }
    .activity-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
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
    .activity-error {
      color: #9f1239;
      background: #fff1f2;
      border: 1px solid #fecdd3;
      border-radius: 6px;
      padding: 4px 6px;
      margin-bottom: 4px;
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
      .form-row { grid-template-columns: 1fr; }
      .simple-home { min-height: auto; padding-top: 22px; }
      .simple-prompt-title { font-size: 23px; }
      .simple-composer-main {
        grid-template-columns: 40px minmax(0, 1fr);
        align-items: end;
      }
      .simple-composer-actions {
        grid-column: 1 / -1;
        justify-content: flex-end;
      }
      .simple-config-grid,
      .simple-config-editors {
        grid-template-columns: 1fr;
      }
      .simple-agent-node { width: 230px; }
      .activity-grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; gap: 4px; }
      .header-actions { justify-content: flex-start; }
      .simple-agent-main { flex-direction: column; }
      .simple-agent-work { white-space: normal; }
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
    <div class="header-actions">
      <div class="mode-toggle" aria-label="Dashboard mode">
        <button data-action="set-dashboard-mode" data-mode="simple" id="mode-simple">Simple</button>
        <button data-action="set-dashboard-mode" data-mode="debug" id="mode-debug">Debug</button>
      </div>
      <div class="module-toggle" aria-label="Dashboard module">
        <button data-action="set-dashboard-module" data-module-name="overview" id="module-overview">Overview</button>
        <button data-action="set-dashboard-module" data-module-name="launch" id="module-launch">Launch</button>
        <button data-action="set-dashboard-module" data-module-name="config" id="module-config">Config</button>
        <button data-action="set-dashboard-module" data-module-name="demos" id="module-demos">Demos</button>
        <button data-action="set-dashboard-module" data-module-name="debug" id="module-debug">Debug</button>
      </div>
      <div class="muted">Stream <span id="stream-status">connecting</span> - State <span id="updated">loading</span></div>
    </div>
  </header>
  <main>
    <section class="grid" id="metrics"></section>
    <section class="grid">
      <div class="panel span-12" data-module="launch">
        <div class="task-designer">
          <div class="demo-panel">
            <div class="demo-copy">
              <div class="demo-title">Designed Task Run</div>
              <div class="muted">Enter a goal, generate an organization/config draft, edit the JSON, then start execution.</div>
            </div>
            <div class="demo-actions">
              <span class="pill" id="designed-task-status">idle</span>
            </div>
          </div>
          <label>Task goal
            <textarea id="designed-task-goal" placeholder="Example: Research the current Python packaging release process and produce a concise implementation plan."></textarea>
          </label>
          <div class="form-row">
            <label>Headcount
              <input id="designed-task-headcount" type="number" min="3" value="6">
            </label>
            <label>Token budget
              <input id="designed-task-token-budget" type="number" min="0" value="600000">
            </label>
            <label>Manager model
              <input id="designed-task-management-model" value="openai/gpt-oss-120b:free">
            </label>
            <label>Worker model
              <input id="designed-task-worker-model" value="poolside/laguna-m.1:free">
            </label>
          </div>
          <div class="demo-actions">
            <button class="primary-button" id="design-task-config" data-action="design-task-config">Design Org/Config</button>
            <button class="primary-button" id="start-designed-task" data-action="start-designed-task">Start Confirmed Task</button>
            <label style="display:inline-flex;grid-auto-flow:column;align-items:center;gap:6px;width:auto;">
              <input id="designed-task-use-llm" type="checkbox" checked style="width:auto;"> use LLM
            </label>
            <label style="display:inline-flex;grid-auto-flow:column;align-items:center;gap:6px;width:auto;">
              Filter task
              <select id="task-filter-select" style="min-width:260px;"><option value="">All tasks</option></select>
            </label>
            <button id="export-task-trace" data-action="export-task-trace">Export Selected Task Trace</button>
            <span class="pill" id="task-trace-export-status">trace idle</span>
          </div>
          <div class="operation-progress" id="designed-task-progress" aria-live="polite">
            <div class="operation-progress-head">
              <div>
                <div class="operation-progress-title" id="designed-task-progress-title">No design request running.</div>
                <div class="operation-progress-detail" id="designed-task-progress-detail">Click Design Org/Config to generate a draft organization.</div>
              </div>
              <span class="pill" id="designed-task-progress-elapsed">idle</span>
            </div>
            <div class="progress-track"><div class="progress-fill"></div></div>
            <div class="progress-steps" id="designed-task-progress-steps"></div>
          </div>
          <div class="draft-org-panel">
            <div class="draft-org-head">
              <div class="draft-org-title">Draft Organization Tree</div>
              <span class="pill" id="draft-org-summary">no draft</span>
            </div>
            <div id="draft-org-tree" class="muted">No draft yet.</div>
          </div>
          <label>Editable config JSON
            <textarea class="config-editor" id="designed-task-config-json" spellcheck="false"></textarea>
          </label>
        </div>
      </div>
      <div class="panel span-12" data-module="config">
        <div class="task-designer">
          <div class="demo-panel">
            <div class="demo-copy">
              <div class="demo-title">Runtime Config</div>
              <div class="muted">Unified Workforce Runtime JSON config. Save writes the effective config back to the configured JSON file.</div>
            </div>
            <div class="demo-actions">
              <button id="load-runtime-config" data-action="load-runtime-config">Load Config</button>
              <button class="primary-button" id="save-runtime-config" data-action="save-runtime-config">Save Config</button>
              <span class="pill" id="runtime-config-status">config idle</span>
            </div>
          </div>
          <textarea class="config-editor" id="runtime-config-json" spellcheck="false"></textarea>
        </div>
      </div>
      <div class="panel span-12" data-module="demos">
        <div class="demo-panel">
          <div class="demo-copy">
            <div class="demo-title">Long RFC Demo</div>
            <div class="muted">Starts a predefined CEO -> COO -> VP -> Manager -> Worker research workflow in this dashboard database.</div>
          </div>
          <div class="demo-actions">
            <button class="primary-button" id="start-long-rfc-demo" data-action="start-long-rfc-demo">Start Long RFC Demo</button>
            <span class="pill" id="long-rfc-demo-status">idle</span>
          </div>
        </div>
      </div>
      <div class="panel span-12" data-module="demos">
        <div class="demo-panel">
          <div class="demo-copy">
            <div class="demo-title">Real LLM Benchmark</div>
            <div class="muted">Runs org_designer plus OpenRouter manager and worker steps, then scores the run in this dashboard database.</div>
          </div>
          <div class="demo-actions">
            <button class="primary-button" id="start-real-llm-benchmark" data-action="start-real-llm-benchmark">Start Real LLM Benchmark</button>
            <span class="pill" id="real-llm-benchmark-status">idle</span>
          </div>
        </div>
      </div>
      <div class="panel span-12" data-module="demos">
        <div class="demo-panel">
          <div class="demo-copy">
            <div class="demo-title">Claude Steer Demo</div>
            <div class="muted">Starts a medium-length interactive Claude Code task. Open the Claude agent and send steering while it runs.</div>
          </div>
          <div class="demo-actions">
            <button class="primary-button" id="start-claude-steer-demo" data-action="start-claude-steer-demo">Start Claude Steer Demo</button>
            <span class="pill" id="claude-steer-demo-status">idle</span>
          </div>
        </div>
      </div>
      <div class="panel span-12 simple-home simple-only" data-module="overview">
        <div class="simple-home-inner">
          <div class="simple-system-line" id="simple-system-line"></div>
          <div class="simple-prompt-title">Where should we begin?</div>
          <div class="simple-composer-shell">
            <div class="simple-composer-main">
              <button class="icon-button" id="simple-config-toggle" data-action="toggle-simple-config" aria-expanded="false" title="Config">+</button>
              <textarea id="simple-task-goal" placeholder="Ask the workforce to do something..."></textarea>
              <div class="simple-composer-actions">
                <span class="pill" id="designed-task-status-simple">idle</span>
                <button class="primary-button" id="simple-design-task-config" data-action="design-task-config">Design Org</button>
                <button class="primary-button simple-run-button" id="simple-start-designed-task" data-action="start-designed-task" hidden>Run</button>
                <span hidden id="simple-task-status">idle</span>
                <button hidden id="start-simple-task" data-action="start-simple-task">Run Direct</button>
              </div>
            </div>
            <div class="simple-config-summary-row">
              <span id="simple-config-summary">Config loading...</span>
              <div class="simple-config-actions">
                <select id="task-filter-select-simple" aria-label="Filter task"><option value="">All tasks</option></select>
                <button id="export-task-trace-simple" data-action="export-task-trace">Export Trace</button>
                <span class="pill" id="task-trace-export-status-simple">trace idle</span>
              </div>
            </div>
            <div class="simple-config-panel" id="simple-config-panel">
              <div class="simple-config-grid">
                <label>Headcount
                  <input id="simple-designed-task-headcount" type="number" min="3" value="6">
                </label>
                <label>Token budget
                  <input id="simple-designed-task-token-budget" type="number" min="0" value="600000">
                </label>
                <label>Manager model
                  <input id="simple-designed-task-management-model" value="openai/gpt-oss-120b:free">
                </label>
                <label>Worker model
                  <input id="simple-designed-task-worker-model" value="poolside/laguna-m.1:free">
                </label>
                <label style="display:inline-flex;grid-auto-flow:column;align-items:center;gap:6px;width:auto;">
                  <input id="simple-designed-task-use-llm" type="checkbox" checked style="width:auto;"> use LLM
                </label>
                <button id="load-runtime-config-simple" data-action="load-runtime-config">Load Config</button>
                <button class="primary-button" id="save-runtime-config-simple" data-action="save-runtime-config">Save Config</button>
                <span class="pill" id="runtime-config-status-simple">config idle</span>
              </div>
              <div class="simple-config-editors">
                <label>Runtime config JSON
                  <textarea class="config-editor" id="runtime-config-json-simple" spellcheck="false"></textarea>
                </label>
                <label>Generated task config JSON
                  <textarea class="config-editor" id="simple-designed-task-config-json" spellcheck="false"></textarea>
                </label>
              </div>
            </div>
          </div>
          <div class="operation-progress" id="simple-designed-task-progress" aria-live="polite">
            <div class="operation-progress-head">
              <div>
                <div class="operation-progress-title" id="simple-designed-task-progress-title">No design request running.</div>
                <div class="operation-progress-detail" id="simple-designed-task-progress-detail">Ready.</div>
              </div>
              <span class="pill" id="simple-designed-task-progress-elapsed">idle</span>
            </div>
            <div class="progress-track"><div class="progress-fill"></div></div>
            <div class="progress-steps" id="simple-designed-task-progress-steps"></div>
          </div>
          <div class="simple-draft-shell" id="simple-draft-shell">
            <div class="draft-org-head">
              <div class="draft-org-title">Draft Organization Tree</div>
              <span class="pill" id="simple-draft-org-summary">no draft</span>
            </div>
            <div id="simple-draft-org-tree" class="muted">No draft yet.</div>
          </div>
          <div class="simple-task-report" id="simple-task-report"></div>
        </div>
      </div>
      <div class="panel span-12" data-module="overview">
        <div class="org-toolbar">
          <h2>Org Chart</h2>
          <div class="muted" id="org-summary"></div>
        </div>
        <div class="communication-pulses simple-only" id="communication-pulses"></div>
        <div id="org-chart"></div>
      </div>
      <div class="panel span-12" data-module="overview">
        <h2>Human Reports</h2>
        <div class="human-reports" id="human-reports"></div>
      </div>
      <div class="panel span-12 debug-only" data-module="debug">
        <h2>Internal Manager Reports</h2>
        <div class="manager-reports" id="manager-reports"></div>
      </div>
      <div class="panel span-6 debug-only" data-module="debug">
        <h2>Agents</h2>
        <table id="agents"></table>
      </div>
      <div class="panel span-6 debug-only" data-module="debug">
        <h2>Tasks</h2>
        <table id="tasks"></table>
      </div>
      <div class="panel span-12 debug-only" data-module="debug">
        <h2>Work Queue</h2>
        <table id="work-queue"></table>
      </div>
      <div class="panel span-6 debug-only" data-module="debug">
        <h2>Agent Runs</h2>
        <table id="runs"></table>
      </div>
      <div class="panel span-6 debug-only" data-module="debug">
        <h2>Reports</h2>
        <table id="reports"></table>
      </div>
      <div class="panel span-12 debug-only" data-module="debug">
        <h2>Live Agent Output</h2>
        <div id="output"></div>
      </div>
      <div class="panel span-6 debug-only" data-module="debug">
        <h2>Replay</h2>
        <pre id="replay"></pre>
      </div>
      <div class="panel span-6 debug-only" data-module="debug">
        <h2>Trajectories</h2>
        <pre id="trajectories"></pre>
      </div>
    </section>
  </main>
  <div class="detail-backdrop" id="agent-backdrop" hidden data-action="close-detail"></div>
  <aside class="detail-drawer" id="agent-detail" aria-hidden="true"></aside>
  <script>
    const DEFAULT_CONFIG = {
      dashboard: { refresh_interval_ms: 5000, max_visible_agents: 80, simple_level_agent_limit: 8, state_agent_limit: 60, collapse_depth: 3, show_idle_activity: true },
      activity: { recent_output_items: 12, recent_tool_items: 12, recent_event_items: 10, full_stream_limit: 200, global_output_limit: 200 },
      summaries: { mode: "local", max_chars: 140 },
      queue: { max_active_agents: 20, lease_seconds: 300, per_kind_limits: { llm_request: 10, tool_call: 20, worker_run: 10 } }
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
      "work_item_enqueued",
      "work_item_deduplicated",
      "work_item_claimed",
      "work_item_completed",
      "work_item_failed",
      "work_item_requeued",
      "work_item_cancelled",
      "work_item_lease_expired",
      "work_item_execution_started",
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
      "simple_task_run_failed",
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
    let agentDetails = {};
    let agentDetailRequests = new Set();
    let expandedSummaryAgents = new Set();
    let agentActivity = {};
    let steerableSessions = [];
    let dashboardConfig = structuredClone(DEFAULT_CONFIG);
    let expandedNodes = new Set();
    let collapsedNodes = new Set();
    let selectedAgentId = null;
    let selectedTaskId = "";
    let currentTaskScope = new Set();
    let visibleNodeCount = 0;
    let dashboardMode = localStorage.getItem("workforceDashboardMode") || "simple";
    let dashboardModule = localStorage.getItem("workforceDashboardModule") || "overview";
    let communicationPulses = [];
    let longRfcDemoStatus = { status: "idle", running: false };
    let realLlmBenchmarkStatus = { status: "idle", running: false };
    let claudeSteerDemoStatus = { status: "idle", running: false };
    let simpleTaskStatus = { status: "idle", running: false };
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

    function byId(id) {
      return document.getElementById(id);
    }

    function activeField(debugId, simpleId) {
      const simple = byId(simpleId);
      const debug = byId(debugId);
      if (dashboardMode === "simple" && simple) return simple;
      return debug || simple;
    }

    function fieldValue(debugId, simpleId, fallback = "") {
      const element = activeField(debugId, simpleId);
      return element?.value ?? fallback;
    }

    function fieldChecked(debugId, simpleId, fallback = false) {
      const element = activeField(debugId, simpleId);
      return element ? Boolean(element.checked) : fallback;
    }

    function setBothValues(debugId, simpleId, value) {
      for (const id of [debugId, simpleId]) {
        const element = byId(id);
        if (element && value != null) element.value = value;
      }
    }

    function setBothChecked(debugId, simpleId, value) {
      for (const id of [debugId, simpleId]) {
        const element = byId(id);
        if (element && value != null) element.checked = Boolean(value);
      }
    }

    function clip(value, limit = cfg("summaries", "max_chars", 140)) {
      const text = String(value ?? "").replace(/\s+/g, " ").trim();
      return text.length > limit ? `${text.slice(0, Math.max(limit - 3, 1))}...` : text;
    }

    function clipTail(value, limit = cfg("summaries", "max_chars", 140)) {
      const text = String(value ?? "").replace(/\s+/g, " ").trim();
      return text.length > limit ? `...${text.slice(-Math.max(limit - 3, 1))}` : text;
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
        resultText: claudeSteerDemoStatus?.task_id ? ` task=${claudeSteerDemoStatus.task_id}` : "",
      });
      renderSimpleTaskStatus();
      const designedResult = designedTaskStatus?.result;
      renderRunStatus({
        labelId: "designed-task-status",
        buttonId: "start-designed-task",
        statusPayload: designedTaskStatus,
        resultText: designedResult?.root_task_id ? ` root=${designedResult.root_task_id}` : designedTaskStatus?.root_task_id ? ` root=${designedTaskStatus.root_task_id}` : "",
      });
      renderDesignedStatusControls();
    }

    function renderDesignedStatusControls() {
      const status = designedTaskStatus?.status || "idle";
      const rootTask = designedTaskStatus?.root_task_id || designedTaskStatus?.result?.root_task_id || "";
      const error = designedTaskStatus?.error ? ` error=${clip(designedTaskStatus.error, 90)}` : "";
      const labelText = `${status}${rootTask ? " root=" + rootTask : ""}${error}`;
      for (const id of ["designed-task-status-simple"]) {
        const label = document.getElementById(id);
        if (label) label.textContent = labelText;
      }
      for (const id of ["design-task-config", "simple-design-task-config"]) {
        const button = document.getElementById(id);
        if (button) button.disabled = Boolean(designedTaskStatus?.running);
      }
      for (const id of ["simple-start-designed-task"]) {
        const button = document.getElementById(id);
        if (button) {
          button.disabled = Boolean(designedTaskStatus?.running);
          button.hidden = !designedTaskConfig;
        }
      }
    }

    function renderSimpleTaskStatus() {
      const label = document.getElementById("simple-task-status");
      const button = document.getElementById("start-simple-task");
      const report = document.getElementById("simple-task-report");
      if (!label || !button || !report) return;
      const status = simpleTaskStatus?.status || "idle";
      const taskId = simpleTaskStatus?.task_id || simpleTaskStatus?.result?.task_id || "";
      const error = simpleTaskStatus?.error ? ` error=${clip(simpleTaskStatus.error, 90)}` : "";
      label.textContent = `${status}${taskId ? " task=" + taskId : ""}${error}`;
      button.disabled = Boolean(simpleTaskStatus?.running);
      const result = simpleTaskStatus?.result || {};
      const reportText = result.report_text || "";
      const reportPath = result.result_path || "";
      if (simpleTaskStatus?.running) {
        report.innerHTML = `<div class="simple-task-report-card">
          <div class="human-report-head">
            <div>
              <div class="human-report-title">Running</div>
              <div class="human-report-meta">${esc(taskId || "task is being created")}</div>
            </div>
            <span class="pill">live</span>
          </div>
          <div class="simple-task-report-text">The organization is working on the task. Open the Claude worker in the org chart to steer it while it runs.</div>
        </div>`;
        return;
      }
      if (status === "completed" || status === "failed") {
        const pathLink = reportPath ? ` ${renderFileLink(reportPath, "result")}` : "";
        report.innerHTML = `<div class="simple-task-report-card">
          <div class="human-report-head">
            <div>
              <div class="human-report-title">${status === "completed" ? "Final Report" : "Run Failed"}</div>
              <div class="human-report-meta">${esc(taskId || "-")}${pathLink}</div>
            </div>
            <span class="pill">${esc(status)}</span>
          </div>
          <div class="simple-task-report-text">${esc(reportText || simpleTaskStatus.error || "No report text was recorded.")}</div>
        </div>`;
        return;
      }
      report.innerHTML = "";
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
      renderDesignedProgressView("designed-task");
      renderDesignedProgressView("simple-designed-task");
    }

    function renderDesignedProgressView(prefix) {
      const panel = document.getElementById(`${prefix}-progress`);
      if (!panel) return;
      const title = document.getElementById(`${prefix}-progress-title`);
      const detail = document.getElementById(`${prefix}-progress-detail`);
      const elapsed = document.getElementById(`${prefix}-progress-elapsed`);
      const steps = document.getElementById(`${prefix}-progress-steps`);
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
      const [longRes, benchmarkRes, claudeSteerRes, simpleRes, designedRes] = await Promise.all([
        fetch("/api/demos/long-rfc/status", { cache: "no-store" }),
        fetch("/api/demos/real-llm-benchmark/status", { cache: "no-store" }),
        fetch("/api/demos/claude-steer/status", { cache: "no-store" }),
        fetch("/api/simple-task/status", { cache: "no-store" }),
        fetch("/api/designed-task/status", { cache: "no-store" }),
      ]);
      longRfcDemoStatus = await longRes.json();
      realLlmBenchmarkStatus = await benchmarkRes.json();
      claudeSteerDemoStatus = await claudeSteerRes.json();
      simpleTaskStatus = await simpleRes.json();
      const serverDesignedTaskStatus = await designedRes.json();
      if (designedTaskStatus?.status !== "designing") {
        designedTaskStatus = serverDesignedTaskStatus;
      }
      if (designedTaskStatus?.root_task_id && !selectedTaskId) {
        selectedTaskId = designedTaskStatus.root_task_id;
        scheduleRefresh(0);
      }
      if (simpleTaskStatus?.task_id && !selectedTaskId) {
        selectedTaskId = simpleTaskStatus.task_id;
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
      dashboardModule = "overview";
      localStorage.setItem("workforceDashboardModule", dashboardModule);
      renderDemoStatus();
      renderModuleControls();
      const res = await fetch("/api/demos/claude-steer/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      claudeSteerDemoStatus = await res.json();
      if (!res.ok && !claudeSteerDemoStatus.error) {
        claudeSteerDemoStatus.error = `HTTP ${res.status}`;
      }
      if (claudeSteerDemoStatus?.task_id) {
        selectedTaskId = claudeSteerDemoStatus.task_id;
      }
      renderDemoStatus();
      await refresh();
    }

    async function startSimpleTask() {
      const input = document.getElementById("simple-task-goal");
      const goal = input?.value?.trim() || "";
      if (!goal) {
        simpleTaskStatus = { status: "failed", running: false, error: "Enter a task first." };
        renderSimpleTaskStatus();
        return;
      }
      simpleTaskStatus = { status: "starting", running: true, goal };
      selectedTaskId = "";
      currentTaskScope = new Set();
      dashboardMode = "simple";
      dashboardModule = "overview";
      localStorage.setItem("workforceDashboardMode", dashboardMode);
      localStorage.setItem("workforceDashboardModule", dashboardModule);
      renderModeControls();
      renderSimpleTaskStatus();
      const res = await fetch("/api/simple-task/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal })
      });
      simpleTaskStatus = await res.json();
      if (!res.ok && !simpleTaskStatus.error) {
        simpleTaskStatus.error = `HTTP ${res.status}`;
      }
      if (simpleTaskStatus?.task_id) {
        selectedTaskId = simpleTaskStatus.task_id;
      }
      renderSimpleTaskStatus();
      await refresh();
    }

    async function designTaskConfig() {
      const goal = fieldValue("designed-task-goal", "simple-task-goal", "").trim();
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
      setBothValues("designed-task-goal", "simple-task-goal", goal);
      designedTaskStatus = { status: "designing", running: true };
      renderDemoStatus();
      const payload = {
        goal,
        headcount_limit: Number(fieldValue("designed-task-headcount", "simple-designed-task-headcount", runtimeConfig?.designed_task?.headcount_limit || 6)),
        token_budget: Number(fieldValue("designed-task-token-budget", "simple-designed-task-token-budget", runtimeConfig?.designed_task?.token_budget || 600000)),
        management_model: fieldValue("designed-task-management-model", "simple-designed-task-management-model", "").trim() || runtimeConfig?.designed_task?.management_model || "openai/gpt-oss-120b:free",
        worker_model: fieldValue("designed-task-worker-model", "simple-designed-task-worker-model", "").trim() || runtimeConfig?.designed_task?.worker_model || "poolside/laguna-m.1:free",
        use_llm: fieldChecked("designed-task-use-llm", "simple-designed-task-use-llm", true),
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
      setBothValues("designed-task-config-json", "simple-designed-task-config-json", JSON.stringify(designedTaskConfig, null, 2));
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
      const editor = activeField("designed-task-config-json", "simple-designed-task-config-json");
      let config;
      try {
        const text = editor?.value?.trim() || "";
        config = text ? JSON.parse(text) : designedTaskConfig;
        if (!config) throw new Error("Design an organization first.");
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
      setBothValues("designed-task-config-json", "simple-designed-task-config-json", JSON.stringify(config, null, 2));
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
      for (const id of ["runtime-config-status", "runtime-config-status-simple"]) {
        const status = document.getElementById(id);
        if (status) status.textContent = text;
      }
    }

    function setTraceExportStatus(html) {
      for (const id of ["task-trace-export-status", "task-trace-export-status-simple"]) {
        const status = document.getElementById(id);
        if (status) status.innerHTML = html;
      }
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
      setBothValues("runtime-config-json", "runtime-config-json-simple", JSON.stringify(runtimeConfig, null, 2));
      applyRuntimeConfigDefaults();
      setRuntimeConfigStatus(`loaded ${data.path || ""}`.trim());
    }

    async function saveRuntimeConfig() {
      const editor = activeField("runtime-config-json", "runtime-config-json-simple");
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
      setBothValues("runtime-config-json", "runtime-config-json-simple", JSON.stringify(runtimeConfig, null, 2));
      applyRuntimeConfigDefaults();
      setRuntimeConfigStatus(`saved ${data.path || ""}`.trim());
      await refresh();
    }

    function applyRuntimeConfigDefaults() {
      const defaults = runtimeConfig?.designed_task || {};
      setBothValues("designed-task-headcount", "simple-designed-task-headcount", defaults.headcount_limit);
      setBothValues("designed-task-token-budget", "simple-designed-task-token-budget", defaults.token_budget);
      setBothValues("designed-task-management-model", "simple-designed-task-management-model", defaults.management_model);
      setBothValues("designed-task-worker-model", "simple-designed-task-worker-model", defaults.worker_model);
      setBothChecked("designed-task-use-llm", "simple-designed-task-use-llm", defaults.use_llm);
      renderSimpleConfigSummary();
    }

    function renderSimpleConfigSummary() {
      const host = document.getElementById("simple-config-summary");
      if (!host) return;
      const headcount = fieldValue("designed-task-headcount", "simple-designed-task-headcount", runtimeConfig?.designed_task?.headcount_limit || 6);
      const budget = fieldValue("designed-task-token-budget", "simple-designed-task-token-budget", runtimeConfig?.designed_task?.token_budget || 600000);
      const manager = fieldValue("designed-task-management-model", "simple-designed-task-management-model", runtimeConfig?.designed_task?.management_model || "openai/gpt-oss-120b:free");
      const worker = fieldValue("designed-task-worker-model", "simple-designed-task-worker-model", runtimeConfig?.designed_task?.worker_model || "poolside/laguna-m.1:free");
      const useLlm = fieldChecked("designed-task-use-llm", "simple-designed-task-use-llm", runtimeConfig?.designed_task?.use_llm ?? true);
      host.textContent = `${headcount} agents · ${Number(budget || 0).toLocaleString()} tokens · ${manager} → ${worker} · ${useLlm ? "LLM design" : "local design"}`;
    }

    function toggleSimpleConfig() {
      const panel = document.getElementById("simple-config-panel");
      const button = document.getElementById("simple-config-toggle");
      if (!panel || !button) return;
      const open = !panel.classList.contains("open");
      panel.classList.toggle("open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
      button.textContent = open ? "-" : "+";
    }

    function renderSimpleSystemStatus(data, active, completed, failed, queue, totalAgents) {
      const host = document.getElementById("simple-system-line");
      if (!host) return;
      const taskCount = data.tasks?.length || 0;
      const queueText = `${queue.active_agents || 0}/${queue.total || 0} queue`;
      const tokenText = `${Number(data.budget?.tokens_used || 0).toLocaleString()} tokens`;
      host.innerHTML = [
        `${totalAgents} agents`,
        `${active} active`,
        `${completed} completed`,
        `${failed} failed`,
        queueText,
        tokenText,
        `${taskCount} tasks`,
      ].map(item => `<span class="pill">${esc(item)}</span>`).join("");
    }

    function syncGeneratedTaskConfigEditors(sourceId) {
      const source = document.getElementById(sourceId);
      if (!source) return;
      const targetId = sourceId === "simple-designed-task-config-json"
        ? "designed-task-config-json"
        : "simple-designed-task-config-json";
      const target = document.getElementById(targetId);
      if (target && target.value !== source.value) target.value = source.value;
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
      const editor = activeField("designed-task-config-json", "simple-designed-task-config-json");
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
      const views = [
        { treeHost: document.getElementById("draft-org-tree"), summary: document.getElementById("draft-org-summary"), shell: null },
        { treeHost: document.getElementById("simple-draft-org-tree"), summary: document.getElementById("simple-draft-org-summary"), shell: document.getElementById("simple-draft-shell") },
      ].filter(view => view.treeHost && view.summary);
      if (!views.length) return;
      const renderViews = (summaryText, html, hasDraft = false) => {
        for (const view of views) {
          view.summary.textContent = summaryText;
          view.treeHost.innerHTML = html;
          if (view.shell) view.shell.classList.toggle("has-draft", Boolean(hasDraft));
        }
      };
      const { config, error } = readDesignedTaskConfigFromEditor();
      if (error) {
        renderViews("invalid JSON", `<div class="org-placeholder">Cannot render draft tree: ${esc(error)}</div>`, false);
        return;
      }
      const organization = config?.organization || {};
      const agents = Array.isArray(organization.agents) ? organization.agents : [];
      if (!agents.length) {
        renderViews("no draft", `<div class="muted">No draft yet.</div>`, false);
        return;
      }
      const tree = buildDraftAgentTree(agents);
      const companyName = organization.company?.name || config?.case?.title || "designed org";
      renderViews(`${agents.length} agents - ${companyName}`, `<ul class="draft-tree">${tree.map(node => renderDraftAgentNode(node)).join("")}</ul>`, true);
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
      document.getElementById("org-summary").textContent = dashboardMode === "simple"
        ? `${agentCount} agents · ${cfg("dashboard", "simple_level_agent_limit", 8)} shown per level`
        : `${agentCount} agents - ${dashboardMode} mode - collapse depth ${cfg("dashboard", "collapse_depth", 3)}`;
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
      renderModuleControls();
    }

    function renderModuleControls() {
      const activeModule = dashboardMode === "simple" ? "overview" : dashboardModule;
      document.querySelectorAll("[data-module]").forEach(element => {
        element.classList.toggle("module-active", dashboardMode === "debug" || element.dataset.module === activeModule);
      });
      document.querySelectorAll("[data-module-name]").forEach(button => {
        button.classList.toggle("active", button.dataset.moduleName === activeModule);
      });
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
      if ((event.event_type || "").startsWith("human_agent_steer")) {
        const target = payload.target_agent_id || "agent";
        return { kind: payload.status === "no_active_session" ? "report" : "human", text: `${actor} steering ${target}: ${clip(payload.message || payload.action || "", 80)}` };
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
      if (node.placeholder) {
        return `<li class="org-node"><div class="org-placeholder">${esc(node.name || "Agents hidden by dashboard state limit.")}</div></li>`;
      }
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
      const simpleMode = dashboardMode !== "debug";
      const simpleLevelLimit = Math.max(1, Number(cfg("dashboard", "simple_level_agent_limit", 8)));
      const displayedChildren = simpleMode && children.length > simpleLevelLimit
        ? children.slice(0, simpleLevelLimit)
        : children;
      const hiddenAtLevel = simpleMode ? Math.max(0, children.length - displayedChildren.length) : 0;
      const collapsed = isNodeCollapsed(node, depth);
      const toggle = hasChildren
        ? `<button class="tree-toggle" data-action="toggle-node" data-agent-id="${esc(node.id)}" data-depth="${esc(depth)}" title="Toggle reports">${collapsed ? "+" : "-"}</button>`
        : "";
      const childrenMarkup = hasChildren
        ? simpleMode
          ? `<ul class="org-children">${displayedChildren.map(child => renderOrgNode(child, depth + 1)).join("")}${hiddenAtLevel ? `<li class="org-node simple-overflow-node"><div class="org-placeholder">+${esc(hiddenAtLevel)} more</div></li>` : ""}</ul>`
          : collapsed
          ? `<ul class="org-children"><li class="org-node"><div class="org-placeholder">${esc(children.length)} direct report(s), ${esc(node.descendant_count || children.length)} total below.</div></li></ul>`
          : `<ul class="org-children">${children.map(child => renderOrgNode(child, depth + 1)).join("")}</ul>`
        : "";
      if (simpleMode) {
        return renderSimpleOrgNode({ node, depth, summary, active, work, toggle, childrenMarkup, hasChildren });
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

    function renderSimpleOrgNode({ node, summary, active, work, childrenMarkup, hasChildren }) {
      const status = node.status || "idle";
      return `<li class="org-node">
        <div class="agent-node simple-agent-node ${active ? "active" : ""} ${hasChildren ? "has-children" : ""}" data-agent-id="${esc(node.id)}">
          <div class="simple-agent-main">
            <div class="agent-title">
              ${renderAgentIcon(node.icon)}
              <div>
                <div class="agent-name">${esc(node.name || node.id)}</div>
                <div class="simple-agent-role">${esc(node.role || "Agent")} - ${esc(node.worker_type || "")}</div>
              </div>
            </div>
            <div class="agent-controls">
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
      const outputItems = (activity.full_output || activity.output || []);
      const output = aggregateOutputItems(outputItems).slice(-1)[0];
      if (output) {
        const label = output.stream === "error" ? "Error" : (output.stream || "output");
        const outputLimit = Math.max(12, cfg("summaries", "max_chars", 140) - label.length - 2);
        const outputText = String(output.text || "");
        candidates.push({
          timestamp: output.timestamp || "",
          text: `${label}: ${clipTail(outputText, outputLimit)}`,
          full_text: `${label}: ${outputText.replace(/\s+/g, " ").trim()}`,
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
      if (node?.status === "busy" || node?.status === "blocked" || node?.status === "assigned" || node?.status === "in_progress") {
        return {mode: "local", text: `Status: ${node.status}`, active: true};
      }
      return {mode: "local", text: "Idle.", active: false};
    }

    function compactEventDetail(event) {
      const payload = event.payload || {};
      const keys = ["tool_name", "requested_tool_name", "assigned_to", "to_agent_id", "target_agent_id", "profile_agent_id", "agent_id", "work_item_id", "kind", "lease_owner", "action", "status", "stream", "returncode", "timed_out", "report_id", "human_report_id", "decision", "title", "message", "url", "trace_path", "run_dir", "prompt_path", "response_path", "raw_response_path", "error_path", "attempt", "attempts", "max_attempts", "next_attempt", "delay_seconds", "doc_id", "request_id", "revision"];
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

    async function loadAgentDetail(agentId) {
      if (!agentId || agentDetails[agentId] || agentDetailRequests.has(agentId)) return;
      agentDetailRequests.add(agentId);
      try {
        const res = await fetch(`/api/agent?agent_id=${encodeURIComponent(agentId)}`, { cache: "no-store" });
        const payload = await res.json();
        if (payload.ok && payload.agent) {
          agentDetails[agentId] = payload.agent;
          if (selectedAgentId === agentId) renderAgentDetail();
        }
      } catch (err) {
        console.error(err);
      } finally {
        agentDetailRequests.delete(agentId);
      }
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
      const baseNode = findNodeById(selectedAgentId) || agents.find(agent => agent.id === selectedAgentId) || {id: selectedAgentId, name: selectedAgentId, role: "", status: ""};
      const loadedNode = agentDetails[selectedAgentId] || {};
      if (!agentDetails[selectedAgentId] && (baseNode.has_system_prompt || !baseNode.personal_profile)) loadAgentDetail(selectedAgentId);
      const node = {
        ...baseNode,
        ...loadedNode,
        activity: baseNode.activity || loadedNode.activity,
        summary: baseNode.summary || loadedNode.summary,
        children: baseNode.children || [],
      };
      const activity = ensureAgentActivity(selectedAgentId, node.activity);
      const summary = summarizeActivity(activity, node);
      const summaryFullText = summary.full_text || summary.text || "Idle.";
      const summaryExpanded = expandedSummaryAgents.has(selectedAgentId);
      const summaryCanExpand = summaryFullText && summaryFullText !== (summary.text || "");
      const summaryDisplay = summaryExpanded ? summaryFullText : (summary.text || "Idle.");
      const profile = node.personal_profile || {};
      const work = (node.current_task_ids || []).join(", ") || "-";
      const modelLimits = renderModelLimit(node.model_capabilities);
      const systemPrompt = node.system_prompt || (node.has_system_prompt ? "Loading system prompt..." : "No system prompt stored for this agent.");
      const steerSession = steerableSessions.find(session => session.agent_id === selectedAgentId);
      const steerable = Boolean(steerSession);
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
            <div class="agent-summary ${summary.active ? "active" : ""}"><span class="summary-dot"></span><span class="summary-text ${summaryExpanded ? "summary-text-expanded" : ""}">${esc(summaryDisplay)}</span></div>
            ${summaryCanExpand ? `<div class="summary-actions"><button data-action="toggle-summary" data-agent-id="${esc(selectedAgentId)}">${summaryExpanded ? "Show Recent" : "Load Previous"}</button></div>` : ""}
            <div class="agent-meta">tasks: ${esc(work)} - summary mode: ${esc(summary.mode || "local")}</div>
          </div>
          <div class="detail-section">
            <h3>Human Steering</h3>
            <div class="activity-item">${steerable ? `Live session ${esc(steerSession.run_id || "")}` : "No live steerable session. Messages will be recorded but cannot be injected into a finished or one-shot run."}</div>
            <textarea id="agent-steer-message" placeholder="Send a steering message to this agent while it is working."></textarea>
            <div class="demo-actions">
              <button class="primary-button" data-action="send-agent-steer" data-agent-id="${esc(selectedAgentId)}">Send Steering</button>
              <button data-action="interrupt-agent" data-agent-id="${esc(selectedAgentId)}">Interrupt</button>
              <span class="pill" id="agent-steer-status">${steerable ? "live" : "not live"}</span>
            </div>
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
        if (longRfcDemoStatus?.running || realLlmBenchmarkStatus?.running || claudeSteerDemoStatus?.running || simpleTaskStatus?.running || designedTaskStatus?.running) {
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
      steerableSessions = data.steerable_sessions || [];
      currentTaskScope = new Set(data.task_filter?.task_ids || []);
      renderTaskFilterOptions(data.all_tasks || data.tasks || []);
      document.getElementById("mission").textContent = `${data.company.name} - ${data.company.mission || "No mission"}`;
      document.getElementById("updated").textContent = new Date().toLocaleTimeString();
      const active = data.tasks.filter(t => ["assigned", "in_progress", "blocked"].includes(t.status)).length;
      const completed = data.tasks.filter(t => t.status === "completed").length;
      const failed = data.tasks.filter(t => t.status === "failed").length;
      const traceLinks = (data.trace_files || []).slice(-2).map(file => renderFileLink(file.path, file.label || file.run_id || "trace")).join(" ") || "-";
      const queue = data.work_queue || { total: 0, active_agents: 0, status_counts: {}, items: [] };
      const totalAgents = data.agent_count || data.agents.length;
      document.getElementById("metrics").innerHTML = [
        ["Agents", `${totalAgents}${data.budget.headcount_limit ? " / " + data.budget.headcount_limit : ""}`],
        ["Active Tasks", active],
        ["Completed", completed],
        ["Failed", failed],
        ["Tokens", `${data.budget.tokens_used} / ${data.budget.token_budget_limit}`],
        ["Queue", `${queue.active_agents || 0} active / ${queue.total || 0}`],
        ["Trace Files", traceLinks],
        ["Output Events", liveOutput.length],
      ].map(([label, value]) => `<div class="panel span-3"><h2>${esc(label)}</h2><div class="metric">${value}</div></div>`).join("");
      renderSimpleSystemStatus(data, active, completed, failed, queue, totalAgents);
      document.getElementById("agents").innerHTML = rows(["Agent", "Role", "Status", "Model", "Current Work"], data.agents.map(a => [
        `<button data-action="agent-detail" data-agent-detail="${esc(a.id)}">${esc(a.name)}</button>`,
        esc(a.role),
        `<span class="status ${statusClass(a.status)}">${esc(a.status)}</span>`,
        esc(a.model || "-"),
        esc((a.current_task_ids || []).join(", ") || "-")
      ]));
      renderOrgChart();
      renderHumanReports(data.human_reports || []);
      renderManagerReports(data.reports || []);
      document.getElementById("tasks").innerHTML = rows(["Task", "Title", "Status", "Assignee"], data.tasks.map(t => [
        esc(t.task_id),
        esc(t.title),
        `<span class="status ${statusClass(t.status)}">${esc(t.status)}</span>`,
        esc(t.assigned_to || "-")
      ]));
      document.getElementById("work-queue").innerHTML = rows(["Work", "Kind", "Agent", "Status", "Model/Tool", "Attempts", "Lease"], (queue.items || []).slice(-200).map(item => [
        esc(item.work_item_id),
        esc(item.kind || "-"),
        esc(item.agent_id || "-"),
        `<span class="status ${statusClass(item.status)}">${esc(item.status || "-")}</span>`,
        esc(item.tool_name || item.model || "-"),
        esc(`${item.attempts || 0}/${item.max_attempts || 0}`),
        esc(item.lease_owner ? `${item.lease_owner} until ${item.lease_until || "-"}` : "-")
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
      const current = selectedTaskId;
      const html = `<option value="">All tasks</option>` + (tasks || []).map(task => {
        const label = `${task.task_id} - ${task.title || ""}`.slice(0, 140);
        return `<option value="${esc(task.task_id)}">${esc(label)}</option>`;
      }).join("");
      for (const id of ["task-filter-select", "task-filter-select-simple"]) {
        const select = document.getElementById(id);
        if (!select) continue;
        select.innerHTML = html;
        select.value = current;
      }
    }

    function renderFileLink(path, label = "file") {
      if (!path) return "-";
      return `<a href="/api/file?path=${encodeURIComponent(path)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
    }

    async function sendAgentSteer(agentId, action = "message") {
      const status = document.getElementById("agent-steer-status");
      const input = document.getElementById("agent-steer-message");
      const message = input?.value?.trim() || "";
      if (action !== "interrupt" && !message) {
        if (status) status.textContent = "enter message";
        return;
      }
      if (status) status.textContent = "sending";
      const res = await fetch("/api/agents/steer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: agentId,
          task_id: selectedTaskId || undefined,
          message,
          action,
          from_agent_id: "human",
        })
      });
      const payload = await res.json();
      if (status) status.textContent = payload.ok ? payload.status || "sent" : payload.status || payload.error || "failed";
      if (payload.ok && input) input.value = "";
      await refresh();
    }

    document.addEventListener("click", (event) => {
      const simpleCard = event.target.closest(".simple-agent-node[data-agent-id]");
      if (simpleCard && !event.target.closest("button, a, input, textarea, select, [data-action]")) {
        selectedAgentId = simpleCard.dataset.agentId;
        renderAgentDetail();
        return;
      }
      const target = event.target.closest("[data-action]");
      if (!target) return;
      const action = target.dataset.action;
      if (action === "toggle-simple-config") {
        toggleSimpleConfig();
      }
      if (action === "agent-detail") {
        selectedAgentId = target.dataset.agentDetail;
        renderAgentDetail();
      }
      if (action === "close-detail") {
        selectedAgentId = null;
        renderAgentDetail();
      }
      if (action === "toggle-summary") {
        const agentId = target.dataset.agentId || selectedAgentId;
        if (!agentId) return;
        if (expandedSummaryAgents.has(agentId)) {
          expandedSummaryAgents.delete(agentId);
        } else {
          expandedSummaryAgents.add(agentId);
        }
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
        if (dashboardMode === "simple") {
          dashboardModule = "overview";
          localStorage.setItem("workforceDashboardModule", dashboardModule);
        }
        localStorage.setItem("workforceDashboardMode", dashboardMode);
        renderOrgChart();
      }
      if (action === "set-dashboard-module") {
        dashboardModule = target.dataset.moduleName || "overview";
        localStorage.setItem("workforceDashboardModule", dashboardModule);
        renderModuleControls();
      }
      if (action === "send-agent-steer") {
        sendAgentSteer(target.dataset.agentId || selectedAgentId || "").catch(err => {
          const status = document.getElementById("agent-steer-status");
          if (status) status.textContent = String(err);
        });
      }
      if (action === "interrupt-agent") {
        sendAgentSteer(target.dataset.agentId || selectedAgentId || "", "interrupt").catch(err => {
          const status = document.getElementById("agent-steer-status");
          if (status) status.textContent = String(err);
        });
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
      if (action === "start-simple-task") {
        startSimpleTask().catch(err => {
          simpleTaskStatus = { status: "failed", running: false, error: String(err) };
          renderSimpleTaskStatus();
        });
      }
      if (action === "design-task-config") {
        designTaskConfig().catch(err => {
          designedTaskStatus = { status: "failed", running: false, error: String(err) };
          finishDesignedProgress({
            state: "failed",
            phase: "model",
            title: "Design failed.",
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
      if (event.target?.id === "task-filter-select" || event.target?.id === "task-filter-select-simple") {
        selectedTaskId = event.target.value || "";
        for (const id of ["task-filter-select", "task-filter-select-simple"]) {
          const select = document.getElementById(id);
          if (select) select.value = selectedTaskId;
        }
        selectedAgentId = null;
        currentTaskScope = new Set();
        refresh().catch(err => console.error(err));
      }
      if (event.target?.id === "designed-task-use-llm" || event.target?.id === "simple-designed-task-use-llm") {
        setBothChecked("designed-task-use-llm", "simple-designed-task-use-llm", event.target.checked);
        renderSimpleConfigSummary();
      }
    });

    document.addEventListener("input", (event) => {
      const mirrorMap = {
        "designed-task-headcount": "simple-designed-task-headcount",
        "simple-designed-task-headcount": "designed-task-headcount",
        "designed-task-token-budget": "simple-designed-task-token-budget",
        "simple-designed-task-token-budget": "designed-task-token-budget",
        "designed-task-management-model": "simple-designed-task-management-model",
        "simple-designed-task-management-model": "designed-task-management-model",
        "designed-task-worker-model": "simple-designed-task-worker-model",
        "simple-designed-task-worker-model": "designed-task-worker-model",
      };
      if (mirrorMap[event.target?.id]) {
        const mirror = document.getElementById(mirrorMap[event.target.id]);
        if (mirror && mirror.value !== event.target.value) mirror.value = event.target.value;
        renderSimpleConfigSummary();
      }
      if (event.target?.id === "designed-task-config-json" || event.target?.id === "simple-designed-task-config-json") {
        syncGeneratedTaskConfigEditors(event.target.id);
        renderDraftOrganizationTree();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.target?.id === "simple-task-goal" && event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        const action = designedTaskConfig ? startDesignedTask : designTaskConfig;
        action().catch(err => {
          designedTaskStatus = { status: "failed", running: false, error: String(err) };
          finishDesignedProgress({
            state: "failed",
            phase: "request",
            title: "Request failed.",
            detail: String(err),
          });
          renderDemoStatus();
        });
      }
    });

    renderDraftOrganizationTree();
    renderModeControls();
    renderDesignedProgress();
    loadRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
    refresh().catch(err => console.error(err));
  </script>
</body>
</html>
"""
