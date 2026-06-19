from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from workforce_runtime.server.demo import run_sample_repo_fix_demo, run_simple_status_demo, run_web_research_demo


def test_sample_repo_fix_demo_runs_no_tools_and_tool_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.sqlite"
    workspace = tmp_path / "sample_repo"

    output = run_sample_repo_fix_demo(db_path, workspace)

    assert "Workforce Runtime Demo: sample-repo-fix" in output
    assert "Human -> CEO -> VP Engineering -> Engineering Manager -> Codex Worker" in output
    assert "Company goal task: task_001" in output
    assert "VP delegation task: task_002" in output
    assert "No-tools task: task_003" in output
    assert "Tool task: task_005" in output
    assert "Worker Report:" in output
    assert "Fixed boolean parser handling and ran pytest." in output
    assert "Manager Review:" in output
    assert "'decision': 'accept'" in output
    assert "Diff:" in output
    assert "Test log:" in output
    assert "Final status: completed" in output
    assert "Workforce Runtime" in output
    assert (workspace / "artifacts" / "task_005" / "pytest.log").exists()
    assert (workspace / "artifacts" / "task_005" / "diff.patch").exists()
    assert "3 passed" in (workspace / "artifacts" / "task_005" / "pytest.log").read_text()


def test_sample_repo_fix_demo_cli_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.sqlite"
    workspace = tmp_path / "sample_repo"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "demo",
            "sample-repo-fix",
            "--workspace",
            str(workspace),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Final status: completed" in result.stdout
    assert "No-tools task: task_003" in result.stdout
    assert "Tool task: task_005" in result.stdout
    assert "Recent Artifacts:" in result.stdout


def test_simple_status_demo_shows_model_routing_progress_and_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "simple.sqlite"
    workspace = tmp_path / "simple_workspace"

    output = run_simple_status_demo(db_path, workspace)

    assert "Workforce Runtime Demo: simple-status" in output
    assert "Managers: openai/gpt-oss-120b:free" in output
    assert "Terminal worker: poolside/laguna-xs.2:free" in output
    assert "Human -> CEO -> Product Manager -> Laguna Worker" in output
    assert "Progress Check:" in output
    assert "Created concise launch note artifact." in output
    assert "Live Dashboard Snapshots:" in output
    assert "Event Replay" in output
    assert "progress_check_requested" in output
    assert "Agent Trajectories" in output
    assert "Final status: completed" in output
    assert (workspace / "artifacts" / "task_003" / "launch_note.md").exists()


def test_simple_status_demo_cli_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "simple.sqlite"
    workspace = tmp_path / "simple_workspace"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "demo",
            "simple-status",
            "--workspace",
            str(workspace),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Final status: completed" in result.stdout
    assert "Event Replay" in result.stdout


def test_web_research_demo_runs_with_tool_calls_and_artifact(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "web.sqlite"
    workspace = tmp_path / "web_workspace"
    source = tmp_path / "source.html"
    source.write_text(
        "<html><head><title>Example Domain Fixture</title></head>"
        "<body>example.com example.net example.org</body></html>"
    )
    monkeypatch.setenv("WORKFORCE_WEB_RESEARCH_URL", source.as_uri())

    output = run_web_research_demo(db_path, workspace)

    assert "Workforce Runtime Demo: web-research" in output
    assert "Human -> CEO -> VP Engineering -> Engineering Manager -> Codex Worker" in output
    assert "MCP tool-call events:" in output
    assert "Output stream events:" in output
    assert "Final status: completed" in output
    assert "Agent Runs:" in output
    assert "Live Agent Output:" in output
    summary = workspace / "artifacts" / "task_004" / "web_research_summary.md"
    assert summary.exists()
    assert "Example Domain Fixture" in summary.read_text()
