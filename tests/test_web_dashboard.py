from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

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
        runtime.record_agent_run_finished(
            run_id="manager_run_001",
            task_id=task.task_id,
            actor_id="engineering_manager",
            status="completed",
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

        state = build_web_dashboard_state(runtime.store)

    assert state["company"]["name"] == "Demo Workforce"
    assert state["config"]["dashboard"]["refresh_interval_ms"] == 5000
    assert state["tasks"][0]["status"] == "completed"
    assert state["worker_runs"][0]["status"] == "finished"
    assert state["worker_output"][0]["text"] == "streamed line"
    assert state["agent_runs"][1]["kind"] == "agent"
    assert state["agent_output"][-1]["text"] == "manager streamed line"
    assert state["org_chart"][0]["id"] == "ceo"
    engineering = next(child for child in state["org_chart"][0]["children"] if child["id"] == "vp_engineering")
    manager = next(child for child in engineering["children"] if child["id"] == "engineering_manager")
    codex = next(child for child in manager["children"] if child["id"] == "codex_worker")
    assert codex["icon"]["kind"] == "codex"
    assert codex["icon"]["label"] == "Codex"
    assert codex["summary"]["mode"] == "local"
    assert state["agent_activity"]["codex_worker"]["full_output"][0]["text"] == "streamed line"
    assert state["agent_activity"]["engineering_manager"]["tools"][0]["tool_name"] == "assign"
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
        events = json.loads(urlopen(f"http://{host}:{port}/api/events?after=0", timeout=5).read().decode())
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
    assert state["company"]["name"] == "Demo Workforce"
    assert state["config"]["dashboard"]["refresh_interval_ms"] == 1234
    assert config["dashboard"]["collapse_depth"] == 1
    assert "agents" in state
    assert "event_replay" in state
    assert events["cursor"] >= 1
    assert events["events"][0]["event"]["event_type"] == "org_initialized"
    if CODEX_ICON_PATH.exists():
        assert codex_icon.startswith(b"\x89PNG")


def test_dashboard_config_json_merges_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "dashboard.json"
    config_path.write_text(json.dumps({"summaries": {"max_chars": 64}, "icons": {"poolside": {"label": "Laguna"}}}))

    config = load_dashboard_config(config_path)

    assert config["summaries"]["max_chars"] == 64
    assert config["summaries"]["llm"]["model"] == "openai/gpt-oss-120b:free"
    assert config["icons"]["poolside"]["label"] == "Laguna"
    assert config["dashboard"]["max_visible_agents"] == 80
