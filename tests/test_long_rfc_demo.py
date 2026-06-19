from __future__ import annotations

from pathlib import Path

from workforce_runtime.server.long_rfc_demo import run_long_rfc_demo
from workforce_runtime.server.runtime import WorkforceRuntime


def test_long_rfc_demo_runs_with_local_source_and_stream_events(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    source = tmp_path / "rfc.txt"
    source.write_text("RFC fixture text with HTTP semantics and cache control notes.")

    result = run_long_rfc_demo(
        db_path,
        workspace=workspace,
        url=source.as_uri(),
        delay_seconds=0,
    )

    assert result["ok"] is True
    assert result["final_status"] == "completed"
    assert result["worker_returncode"] == 0

    with WorkforceRuntime(db_path) as runtime:
        events = runtime.store.list_events()
        agents = {agent.id: agent for agent in runtime.store.list_agents()}
        artifacts = runtime.store.list_artifacts()

    assert {"ceo", "coo", "vp_research", "research_manager", "codex_worker"} <= set(agents)
    assert agents["codex_worker"].model == "poolside/laguna-m.1:free"
    assert "demo_run_started" in [event.event_type for event in events]
    assert "demo_run_finished" in [event.event_type for event in events]
    assert "worker_output" in [event.event_type for event in events]
    assert any(event.event_type == "mcp_tool_call_started" and event.payload.get("tool_name") == "check_progress" for event in events)
    assert any(artifact.type == "web_research_summary" for artifact in artifacts)
