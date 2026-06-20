from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from workforce_runtime.core import ReportContract, UsageCost
from workforce_runtime.dashboard.config import load_dashboard_config
from workforce_runtime.dashboard.web_dashboard import CODEX_ICON_PATH, build_web_dashboard_state, make_web_dashboard_server
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


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
    assert codex["system_prompt"]
    assert codex["summary"]["mode"] == "local"
    assert state["agent_activity"]["codex_worker"]["full_output"][0]["text"] == "streamed line"
    assert state["agent_activity"]["engineering_manager"]["output"][0]["text"] == "manager streamed line"
    assert state["agent_activity"]["engineering_manager"]["errors"][0]["text"].startswith("OpenRouter stream")
    assert state["agent_activity"]["engineering_manager"]["tools"][0]["tool_name"] == "assign"
    assert state["human_reports"][0]["title"] == "CEO final report"
    assert state["human_reports"][0]["message"] == "Task completed and ready for the human operator."
    assert state["budget"]["tokens_used"] == 42
    assert state["budget"]["tool_calls_used"] == 2
    assert any(item["path"] == str(trace_path) for item in state["trace_files"])
    assert any(item["label"] == "task" and item["task_id"] == task.task_id for item in state["trace_files"])
    assert state["agent_summaries"]["engineering_manager"]["mode"] == "local"
    assert state["agent_summaries"]["engineering_manager"]["text"]
    assert "Event Replay" in state["event_replay"]
    assert "Agent Trajectories" in state["trajectories"]


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
        config = json.loads(urlopen(f"http://{host}:{port}/api/config", timeout=5).read().decode())
        runtime_config = json.loads(urlopen(f"http://{host}:{port}/api/runtime-config", timeout=5).read().decode())
        demo_status = json.loads(urlopen(f"http://{host}:{port}/api/demos/long-rfc/status", timeout=5).read().decode())
        benchmark_status = json.loads(
            urlopen(f"http://{host}:{port}/api/demos/real-llm-benchmark/status", timeout=5).read().decode()
        )
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

    assert "Workforce Runtime Dashboard" in html
    assert "Org Chart" in html
    assert "Live Agent Output" in html
    assert "agent-detail" in html
    assert "Details" in html
    assert "Designed Task Run" in html
    assert "Human Reports" in html
    assert "Internal Manager Reports" in html
    assert "Start Long RFC Demo" in html
    assert "Start Real LLM Benchmark" in html
    assert state["company"]["name"] == "Demo Workforce"
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
    assert "agents" in state
    assert "event_replay" in state
    assert events["cursor"] >= 1
    assert events["events"][0]["event"]["event_type"] == "org_initialized"
    if CODEX_ICON_PATH.exists():
        assert codex_icon.startswith(b"\x89PNG")


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
