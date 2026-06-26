from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from workforce_runtime.core import AgentProfile, ReportContract, UsageCost
from workforce_runtime.dashboard.config import load_dashboard_config
from workforce_runtime.dashboard.web_dashboard import CODEX_ICON_PATH, HTML, build_web_dashboard_state, make_web_dashboard_server
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers.steering import STEERABLE_SESSIONS


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


class FakeSteerableSession:
    def __init__(self, *, run_id: str, task_id: str, agent_id: str) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.agent_id = agent_id
        self.messages: list[str] = []
        self.interrupted = False

    def steer(self, message: str, *, from_agent_id: str = "human") -> None:
        self.messages.append(f"{from_agent_id}:{message}")

    def interrupt(self, *, from_agent_id: str = "human") -> None:
        self.interrupted = True


def test_web_dashboard_html_includes_claude_simple_ui() -> None:
    assert "<title>Workforce Runtime</title>" in HTML
    assert 'id="app-shell"' in HTML
    assert 'id="sidebar"' in HTML
    assert 'id="status-metrics"' in HTML
    assert 'id="pipeline-card"' in HTML
    assert "Where should we begin?" in HTML
    assert 'id="designed-task-goal"' in HTML
    assert 'id="submit-label"' in HTML
    assert "Design Org" in HTML
    assert "New task" in HTML
    assert "Task history" in HTML
    assert "simple-agent-node" in HTML
    assert "simple-agent-summary" in HTML
    assert "renderSimpleOrg" in HTML
    assert "body.mode-debug .simple-only" in HTML
    assert "body.mode-simple .debug-only" in HTML
    assert 'id="designed-task-config-json"' in HTML
    assert 'id="designed-task-progress-detail"' in HTML
    assert 'id="agent-detail"' in HTML
    assert "function sendTaskCeoMessage" in HTML
    assert "refresh().catch" in HTML


def test_web_dashboard_state_includes_status_replay_and_output(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Inspect smoke",
            objective="Inspect OpenRouter smoke result.",
            assign_to="codex_worker",
        )
        runtime.record_worker_run_started(
            run_id="run_001",
            task_id=task.task_id,
            actor_id="codex_worker",
            executable="codex",
        )
        runtime.record_worker_output(
            run_id="run_001",
            task_id=task.task_id,
            actor_id="codex_worker",
            stream="stdout",
            text="streamed line",
        )
        runtime.record_worker_run_finished(
            run_id="run_001",
            task_id=task.task_id,
            actor_id="codex_worker",
            returncode=0,
        )
        runtime.record_agent_run_started(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            adapter="openrouter",
            model="openai/gpt-oss-120b:free",
        )
        runtime.record_agent_output(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            stream="assistant",
            text="manager streamed line",
        )
        runtime.record_agent_output(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            stream="error",
            text="OpenRouter stream returned no assistant content",
        )
        runtime.record_agent_run_finished(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            status="completed",
            usage={"total_tokens": 42, "tool_calls": 2},
        )
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text("{}\n")
        runtime.record_event(
            event_type="trace_file_written",
            actor_id="system",
            payload={"run_id": "manager_run_001", "label": "test", "trace_path": str(trace_path)},
        )
        runtime.record_event(
            event_type="mcp_tool_call_started",
            actor_id="engineering_manager",
            task_id=task.task_id,
            payload={"tool_name": "assign", "to_agent_id": "codex_worker", "message": "delegate smoke inspection"},
        )
        runtime.update_task_status(task.task_id, status="completed", actor_id="codex_worker")
        runtime.register_report(
            ReportContract(
                report_id="report_001",
                from_agent_id="codex_worker",
                to_agent_id="engineering_manager",
                task_id=task.task_id,
                summary="Smoke completed.",
                status="completed",
                confidence=0.9,
                cost=UsageCost(),
            )
        )
        runtime.report_to_human(
            from_agent_id="ceo",
            task_id=task.task_id,
            title="CEO final report",
            message="Task completed and ready for the human operator.",
            status="completed",
            confidence=0.92,
            next_action="Review the trace.",
        )
        runtime.enqueue_work_item(
            actor_id="system",
            agent_id="codex_worker",
            kind="llm_request",
            task_id=task.task_id,
            payload={"prompt": "summarize smoke"},
            model="openai/gpt-oss-120b:free",
        )
        runtime.claim_work_items(lease_owner="dashboard-test", limit=1)

        state = build_web_dashboard_state(runtime.store)

    assert state["company"]["name"] == "Demo Workforce"
    assert state["config"]["dashboard"]["refresh_interval_ms"] == 5000
    assert state["tasks"][0]["status"] == "completed"
    assert state["worker_runs"][0]["status"] == "finished"
    assert state["worker_output"][0]["text"] == "streamed line"
    assert state["agent_runs"][1]["kind"] == "agent"
    assert any(item["text"] == "manager streamed line" for item in state["agent_output"])
    assert state["org_chart"][0]["id"] == "ceo"
    engineering = next(child for child in state["org_chart"][0]["children"] if child["id"] == "vp_engineering")
    manager = next(child for child in engineering["children"] if child["id"] == "engineering_manager")
    codex = next(child for child in manager["children"] if child["id"] == "codex_worker")
    assert codex["icon"]["kind"] == "codex"
    assert codex["icon"]["label"] == "Codex"
    assert codex["has_system_prompt"] is True
    assert codex["system_prompt"] == ""
    assert codex["summary"]["mode"] == "local"
    assert state["agent_activity"]["codex_worker"]["full_output"][0]["text"] == "streamed line"
    assert state["agent_activity"]["engineering_manager"]["output"][0]["text"] == "manager streamed line"
    assert state["agent_activity"]["engineering_manager"]["errors"][0]["text"].startswith("OpenRouter stream")
    assert state["agent_activity"]["engineering_manager"]["tools"][0]["tool_name"] == "assign"
    assert state["human_reports"][0]["title"] == "CEO final report"
    assert state["human_reports"][0]["message"] == "Task completed and ready for the human operator."
    assert state["work_queue"]["total"] == 1
    assert state["work_queue"]["status_counts"]["leased"] == 1
    assert state["work_queue"]["active_agents"] == 1
    assert state["budget"]["tokens_used"] == 42
    assert state["budget"]["tool_calls_used"] == 2
    assert any(item["path"] == str(trace_path) for item in state["trace_files"])
    assert any(item["label"] == "task" and item["task_id"] == task.task_id for item in state["trace_files"])
    assert state["agent_summaries"]["engineering_manager"]["mode"] == "local"
    assert state["agent_summaries"]["engineering_manager"]["text"]
    assert "Event Replay" in state["event_replay"]
    assert "Agent Trajectories" in state["trajectories"]


def test_task_filtered_org_chart_keeps_idle_agents_in_current_org(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        runtime.store.save_agent(
            AgentProfile(
                id="stale_agent",
                name="Stale Agent",
                role="Old Role",
                department="Old Org",
                manager_id="ceo",
                worker_type="generic_cli",
                system_prompt="You are Stale Agent.\nCompany mission: An older unrelated mission.",
            )
        )
        task = runtime.create_task(
            title="CEO-only start",
            objective="Start at the root before subordinates emit task events.",
            assign_to="ceo",
        )

        state = build_web_dashboard_state(runtime.store, task_id_filter=task.task_id)

    def flatten(nodes: list[dict[str, object]]) -> set[str]:
        ids: set[str] = set()
        for node in nodes:
            ids.add(str(node["id"]))
            ids.update(flatten(node.get("children", [])))  # type: ignore[arg-type]
        return ids

    org_ids = flatten(state["org_chart"])
    assert state["agent_count"] == 6
    assert {"ceo", "vp_engineering", "engineering_manager", "codex_worker", "claude_worker", "hr_manager"} <= org_ids
    assert "stale_agent" not in org_ids


def test_web_dashboard_model_migration_clears_stale_agent_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Stale error", objective="Record a stale model error.", assign_to="codex_worker")
        runtime.record_agent_output(
            run_id="run_bad_model",
            task_id=task.task_id,
            actor_id="codex_worker",
            stream="error",
            text="NVIDIA stream failed: DEGRADED function cannot be invoked",
        )
        runtime.record_event(
            event_type="agent_models_migrated",
            actor_id="system",
            payload={
                "changed_count": 1,
                "sample": [
                    {
                        "id": "codex_worker",
                        "old": "deepseek-ai/deepseek-v4-pro",
                        "new": "openai/gpt-oss-120b:free",
                    }
                ],
            },
        )

        state = build_web_dashboard_state(runtime.store)

    assert state["agent_activity"]["codex_worker"]["errors"] == []
    assert state["agent_activity"]["codex_worker"]["full_output"] == []
    assert state["agent_activity"]["codex_worker"]["events"][-1]["event_type"] == "agent_models_migrated"


def test_web_dashboard_summary_aggregates_stream_chunks(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Stream chunks", objective="Stream a sentence.", assign_to="codex_worker")
        for text in ("Hello", " world", "."):
            runtime.record_agent_output(
                run_id="run_stream",
                task_id=task.task_id,
                actor_id="codex_worker",
                stream="assistant",
                text=text,
            )

        state = build_web_dashboard_state(runtime.store)

    summary = state["agent_summaries"]["codex_worker"]["text"]
    assert summary == "assistant: Hello world."
    full_output = state["agent_activity"]["codex_worker"]["full_output"]
    assert [item["text"] for item in full_output] == ["Hello", " world", "."]


def test_web_dashboard_summary_truncates_from_front_and_preserves_full_text(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Long stream chunks", objective="Stream a long sentence.", assign_to="codex_worker")
        runtime.record_agent_output(
            run_id="run_stream",
            task_id=task.task_id,
            actor_id="codex_worker",
            stream="assistant",
            text="This is the beginning of a long streaming update that should not dominate the compact summary. ",
        )
        runtime.record_agent_output(
            run_id="run_stream",
            task_id=task.task_id,
            actor_id="codex_worker",
            stream="assistant",
            text="The important newest ending says final decision accepted and evidence recorded.",
        )

        state = build_web_dashboard_state(runtime.store, config={"summaries": {"max_chars": 80}})

    summary = state["agent_summaries"]["codex_worker"]
    assert summary["text"].startswith("assistant: ...")
    assert summary["text"].endswith("final decision accepted and evidence recorded.")
    assert "This is the beginning" in summary["full_text"]
    assert summary["full_text"].endswith("final decision accepted and evidence recorded.")


def test_web_dashboard_http_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    config_path = tmp_path / "dashboard.json"
    config_path.write_text(json.dumps({"dashboard": {"refresh_interval_ms": 1234, "collapse_depth": 1}}))
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

    try:
        server = make_web_dashboard_server(db_path, host="127.0.0.1", port=0, config_path=config_path)
    except PermissionError as exc:
        pytest.skip(f"sandbox disallows local socket binding: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        html = urlopen(f"http://{host}:{port}/", timeout=5).read().decode()
        state = json.loads(urlopen(f"http://{host}:{port}/api/state", timeout=5).read().decode())
        agent_detail = json.loads(urlopen(f"http://{host}:{port}/api/agent?agent_id=codex_worker", timeout=5).read().decode())
        config = json.loads(urlopen(f"http://{host}:{port}/api/config", timeout=5).read().decode())
        runtime_config = json.loads(urlopen(f"http://{host}:{port}/api/runtime-config", timeout=5).read().decode())
        demo_status = json.loads(urlopen(f"http://{host}:{port}/api/demos/long-rfc/status", timeout=5).read().decode())
        benchmark_status = json.loads(
            urlopen(f"http://{host}:{port}/api/demos/real-llm-benchmark/status", timeout=5).read().decode()
        )
        claude_steer_status = json.loads(
            urlopen(f"http://{host}:{port}/api/demos/claude-steer/status", timeout=5).read().decode()
        )
        simple_task_status = json.loads(urlopen(f"http://{host}:{port}/api/simple-task/status", timeout=5).read().decode())
        empty_simple_task = Request(
            f"http://{host}:{port}/api/simple-task/start",
            data=json.dumps({"goal": ""}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(empty_simple_task, timeout=5).read()
            simple_task_error_status = 0
        except HTTPError as exc:
            simple_task_error_status = exc.code
        events = json.loads(urlopen(f"http://{host}:{port}/api/events?after=0", timeout=5).read().decode())
        runtime_config["config"]["dashboard"]["refresh_interval_ms"] = 2222
        save_config = Request(
            f"http://{host}:{port}/api/runtime-config",
            data=json.dumps({"config": runtime_config["config"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        saved_config = json.loads(urlopen(save_config, timeout=5).read().decode())
        updated_config = json.loads(urlopen(f"http://{host}:{port}/api/config", timeout=5).read().decode())
        if CODEX_ICON_PATH.exists():
            codex_icon = urlopen(f"http://{host}:{port}/assets/agent-icons/codex.png", timeout=5).read()
        else:
            codex_icon = b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "<title>Workforce Runtime</title>" in html
    assert 'id="app-shell"' in html
    assert 'id="sidebar"' in html
    assert 'id="status-metrics"' in html
    assert 'id="pipeline-card"' in html
    assert "Org Chart" in html
    assert "Live Agent Output" in html
    assert "agent-detail" in html
    assert "Details" in html
    assert "Where should we begin?" in html
    assert "designed-task-goal" in html
    assert "submit-label" in html
    assert "Human Reports" in html
    assert "Internal Manager Reports" in html
    assert "Start Long RFC Demo" in html
    assert "Start Real LLM Benchmark" in html
    assert "Start Claude Steer Demo" in html
    assert state["company"]["name"] == "Demo Workforce"
    assert agent_detail["ok"] is True
    assert agent_detail["agent"]["system_prompt"]
    assert state["config"]["dashboard"]["refresh_interval_ms"] == 1234
    assert config["dashboard"]["collapse_depth"] == 1
    assert runtime_config["ok"] is True
    assert runtime_config["config"]["models"]["openai/gpt-oss-120b:free"]["provider"] == "openrouter"
    assert saved_config["ok"] is True
    assert updated_config["dashboard"]["refresh_interval_ms"] == 2222
    assert demo_status["demo"] == "long-rfc"
    assert demo_status["status"] == "idle"
    assert benchmark_status["demo"] == "real-llm-benchmark"
    assert benchmark_status["status"] == "idle"
    assert claude_steer_status["demo"] == "claude-steer"
    assert claude_steer_status["status"] == "idle"
    assert simple_task_status["kind"] == "simple-task"
    assert simple_task_status["status"] == "idle"
    assert simple_task_error_status == 400
    assert "agents" in state
    assert "event_replay" in state
    assert events["cursor"] >= 1
    assert events["events"][0]["event"]["event_type"] == "org_initialized"
    if CODEX_ICON_PATH.exists():
        assert codex_icon.startswith(b"\x89PNG")


def test_web_dashboard_agent_steer_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Steer task", objective="Wait for steering.", assign_to="claude_worker")

    fake = FakeSteerableSession(run_id="run_steer", task_id=task.task_id, agent_id="claude_worker")
    STEERABLE_SESSIONS.register(fake)
    try:
        try:
            server = make_web_dashboard_server(db_path, host="127.0.0.1", port=0)
        except PermissionError as exc:
            pytest.skip(f"sandbox disallows local socket binding: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            steer_request = Request(
                f"http://{host}:{port}/api/agents/steer",
                data=json.dumps(
                    {
                        "agent_id": "claude_worker",
                        "task_id": task.task_id,
                        "message": "Please switch to the shorter implementation.",
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            steer_response = json.loads(urlopen(steer_request, timeout=5).read().decode())
            state = json.loads(urlopen(f"http://{host}:{port}/api/state", timeout=5).read().decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    finally:
        STEERABLE_SESSIONS.unregister(agent_id="claude_worker", task_id=task.task_id)

    assert steer_response["ok"] is True
    assert fake.messages == ["human:Please switch to the shorter implementation."]
    assert state["steerable_sessions"][0]["agent_id"] == "claude_worker"
    with WorkforceRuntime(db_path) as runtime:
        event_types = [event.event_type for event in runtime.store.list_events()]
    assert "human_agent_steer_requested" in event_types


def test_web_dashboard_agent_steer_queues_for_running_exec_without_live_session(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Queued steer task", objective="Wait for provider session.", assign_to="codex_worker")
        runtime.record_worker_run_started(
            run_id="run_running",
            task_id=task.task_id,
            actor_id="codex_worker",
            executable="codex",
        )

    try:
        server = make_web_dashboard_server(db_path, host="127.0.0.1", port=0)
    except PermissionError as exc:
        pytest.skip(f"sandbox disallows local socket binding: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        steer_request = Request(
            f"http://{host}:{port}/api/agents/steer",
            data=json.dumps(
                {
                    "agent_id": "codex_worker",
                    "task_id": task.task_id,
                    "message": "Apply this after the current exec has a session id.",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        steer_response = json.loads(urlopen(steer_request, timeout=5).read().decode())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert steer_response["ok"] is True
    assert steer_response["status"] == "queued_for_resume"
    assert steer_response["task_id"] == task.task_id
    with WorkforceRuntime(db_path) as runtime:
        queued = [event for event in runtime.store.list_events() if event.event_type == "human_agent_steer_queued"]
    assert queued[0].payload["target_agent_id"] == "codex_worker"
    assert queued[0].payload["message"] == "Apply this after the current exec has a session id."


def test_web_dashboard_agent_steer_starts_idle_openrouter_chat(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "runtime.sqlite"
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        manager = runtime.get_agent("engineering_manager")
        assert manager is not None
        runtime.store.save_agent(
            manager.model_copy(update={"worker_type": "openrouter_manager", "model": "openai/gpt-oss-120b:free"})
        )
        task = runtime.create_task(title="Idle manager chat", objective="Answer a human steering question.", assign_to="engineering_manager")
        runtime.update_task_status(task.task_id, status="completed", actor_id="engineering_manager")

    class FakeRoutedLLMClient:
        def chat(self, **kwargs):
            on_delta = kwargs.get("on_delta")
            if on_delta is not None:
                on_delta("Idle manager answer.")
            return SimpleNamespace(content="Idle manager answer.", usage={"total_tokens": 3})

    monkeypatch.setattr("workforce_runtime.dashboard.web_dashboard.RoutedLLMClient", FakeRoutedLLMClient)

    try:
        server = make_web_dashboard_server(db_path, host="127.0.0.1", port=0)
    except PermissionError as exc:
        pytest.skip(f"sandbox disallows local socket binding: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        steer_request = Request(
            f"http://{host}:{port}/api/agents/steer",
            data=json.dumps(
                {
                    "agent_id": "engineering_manager",
                    "task_id": task.task_id,
                    "message": "What happened on this task?",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        steer_response = json.loads(urlopen(steer_request, timeout=5).read().decode())
        deadline = time.monotonic() + 5
        event_types: list[str] = []
        while time.monotonic() < deadline:
            with WorkforceRuntime(db_path) as runtime:
                event_types = [event.event_type for event in runtime.store.list_events()]
            if "human_agent_steer_sent" in event_types:
                break
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert steer_response["ok"] is True
    assert steer_response["status"] == "idle_chat_started"
    assert "agent_run_started" in event_types
    assert "agent_output" in event_types
    assert "agent_run_finished" in event_types
    assert "human_agent_steer_sent" in event_types


def test_web_dashboard_can_design_start_and_filter_task_run(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"

    try:
        server = make_web_dashboard_server(db_path, host="127.0.0.1", port=0)
    except PermissionError as exc:
        pytest.skip(f"sandbox disallows local socket binding: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        design_request = Request(
            f"http://{host}:{port}/api/designed-task/design",
            data=json.dumps(
                {
                    "goal": "Write a short local fixture task result.",
                    "headcount_limit": 4,
                    "token_budget": 200000,
                    "use_llm": False,
                    "worker_model": "poolside/laguna-m.1:free",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        draft = json.loads(urlopen(design_request, timeout=5).read().decode())
        assert draft["ok"] is True
        assert draft["config"]["case"]["goal"] == "Write a short local fixture task result."
        assert draft["config"]["case"]["headcount_limit"] == 4
        assert draft["config"]["organization"]["company"]["headcount_limit"] == 4
        assert len(draft["config"]["organization"]["agents"]) == 4
        assert draft["config"]["organization"]["agents"]

        draft["config"]["run"]["use_llm"] = False
        draft["config"]["run"]["judge"] = "heuristic"
        draft["config"]["run"]["reset"] = False
        start_request = Request(
            f"http://{host}:{port}/api/designed-task/start",
            data=json.dumps({"config": draft["config"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = json.loads(urlopen(start_request, timeout=5).read().decode())
        assert started["status"] == "running"

        status = started
        deadline = time.monotonic() + 10
        while status["running"] and time.monotonic() < deadline:
            time.sleep(0.1)
            status = json.loads(urlopen(f"http://{host}:{port}/api/designed-task/status", timeout=5).read().decode())
        assert status["status"] == "completed"
        root_task_id = status["root_task_id"]
        assert root_task_id

        filtered = json.loads(
            urlopen(f"http://{host}:{port}/api/state?task_id={root_task_id}", timeout=5).read().decode()
        )
        export_request = Request(
            f"http://{host}:{port}/api/tasks/export-trace",
            data=json.dumps({"task_id": root_task_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        exported = json.loads(urlopen(export_request, timeout=5).read().decode())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert filtered["task_filter"]["enabled"] is True
    assert root_task_id in filtered["task_filter"]["task_ids"]
    assert all(task["task_id"] in filtered["task_filter"]["task_ids"] for task in filtered["tasks"])
    assert filtered["agents"]
    assert filtered["trace_files"]
    assert any(item["label"] == "benchmark" for item in filtered["trace_files"])
    assert exported["ok"] is True
    assert exported["trace"]["task_id"] == root_task_id
    assert Path(exported["path"]).exists()


def test_dashboard_config_json_merges_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "dashboard.json"
    config_path.write_text(json.dumps({"summaries": {"max_chars": 64}, "icons": {"poolside": {"label": "Laguna"}}}))

    config = load_dashboard_config(config_path)

    assert config["summaries"]["max_chars"] == 64
    assert config["summaries"]["llm"]["model"] == "openai/gpt-oss-120b:free"
    assert config["queue"]["max_active_agents"] == 20
    assert config["icons"]["poolside"]["label"] == "Laguna"
    assert config["dashboard"]["max_visible_agents"] == 80


def test_web_dashboard_can_start_long_rfc_demo(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    source = tmp_path / "source.txt"
    source.write_text("Local RFC fixture.")

    try:
        server = make_web_dashboard_server(db_path, host="127.0.0.1", port=0)
    except PermissionError as exc:
        pytest.skip(f"sandbox disallows local socket binding: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        request = Request(
            f"http://{host}:{port}/api/demos/long-rfc/start",
            data=json.dumps({"url": source.as_uri(), "delay_seconds": 0}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = json.loads(urlopen(request, timeout=5).read().decode())
        assert started["status"] == "running"

        status = started
        deadline = time.monotonic() + 10
        while status["running"] and time.monotonic() < deadline:
            time.sleep(0.1)
            status = json.loads(urlopen(f"http://{host}:{port}/api/demos/long-rfc/status", timeout=5).read().decode())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status["status"] == "completed"
    assert status["result"]["final_status"] == "completed"
