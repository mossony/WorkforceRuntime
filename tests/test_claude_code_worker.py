from __future__ import annotations

import subprocess
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import ClaudeCodeWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def write_fake_claude(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

Path("README.md").write_text("# Sample\\n\\nUpdated by fake Claude Code.\\n")
print(json.dumps({
    "result": "Fake Claude Code completed the task.",
    "session_id": "claude_fake",
    "usage": {
        "input_tokens": 12,
        "output_tokens": 7
    }
}))
"""
    )
    path.chmod(path.stat().st_mode | 0o111)


def test_claude_code_worker_captures_outputs_diff_report_and_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    (workspace / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)

    fake_claude = tmp_path / "fake_claude.py"
    write_fake_claude(fake_claude)

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Update README with Claude",
            objective="Update the README and report what changed.",
            assign_to="claude_worker",
        )
        worker = ClaudeCodeWorker(claude_executable=str(fake_claude), timeout_seconds=10)
        run = worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="claude_worker",
                manager_id="engineering_manager",
            ),
        )

        assert run.returncode == 0
        assert run.provider_session_id == "claude_fake"
        assert run.resume_command == "claude -p --resume claude_fake"
        artifact_names = {path.name for path in worker.collect_artifacts(run.run_id)}
        assert {"task_contract.json", "stdout.log", "stderr.log", "claude-final.md", "diff.patch"} <= artifact_names
        assert worker.get_usage(run.run_id) == {"input_tokens": 12, "output_tokens": 7}

        reports = runtime.store.list_reports_by_task(task.task_id)
        assert len(reports) == 1
        assert reports[0].summary == "Fake Claude Code completed the task."
        assert reports[0].cost.tokens_used == 19
        assert runtime.require_task(task.task_id).status == "completed"

        artifact_types = {artifact.type for artifact in runtime.store.list_artifacts_by_task(task.task_id)}
        assert {"claude_final_message", "git_diff"} <= artifact_types

        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "worker_run_started" in event_types
        assert "worker_output" in event_types
        assert "worker_run_finished" in event_types
        assert "provider_session_registered" in event_types
