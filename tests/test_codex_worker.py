from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import CodexWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def write_fake_codex(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

workspace = Path(".")
final_path = None
args = sys.argv[1:]
for index, arg in enumerate(args):
    if arg == "-C":
        workspace = Path(args[index + 1])
    if arg == "--output-last-message":
        final_path = Path(args[index + 1])

(workspace / "README.md").write_text("# Sample\\n\\nUpdated by fake Codex.\\n")
if final_path is not None:
    final_path.write_text("Fake Codex completed the task.")

print(json.dumps({"type": "thread.started", "thread_id": "fake"}))
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 10,
    "cached_input_tokens": 2,
    "output_tokens": 5,
    "reasoning_output_tokens": 3
}}))
"""
    )
    path.chmod(path.stat().st_mode | 0o111)


def test_codex_worker_captures_outputs_diff_report_and_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    (workspace / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)

    fake_codex = tmp_path / "fake_codex.py"
    write_fake_codex(fake_codex)

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Update README",
            objective="Update the README and report what changed.",
            assign_to="codex_worker",
        )
        worker = CodexWorker(codex_executable=str(fake_codex), profile="test", timeout_seconds=10)
        run = worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        assert run.returncode == 0
        artifact_names = {path.name for path in worker.collect_artifacts(run.run_id)}
        assert {"task_contract.json", "stdout.log", "stderr.log", "codex-final.md", "diff.patch"} <= artifact_names
        assert worker.get_usage(run.run_id) == {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 5,
            "reasoning_output_tokens": 3,
        }

        reports = runtime.store.list_reports_by_task(task.task_id)
        assert len(reports) == 1
        assert reports[0].summary == "Fake Codex completed the task."
        assert reports[0].cost.tokens_used == 15
        assert runtime.require_task(task.task_id).status == "completed"

        artifact_types = {artifact.type for artifact in runtime.store.list_artifacts_by_task(task.task_id)}
        assert {"codex_final_message", "git_diff"} <= artifact_types

        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "report_registered" in event_types
        assert "artifact_registered" in event_types
        assert "worker_run_started" in event_types
        assert "worker_output" in event_types
        assert "worker_run_finished" in event_types
