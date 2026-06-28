from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import CodexWorker, RuntimeContext
from workforce_runtime.workers.session_resume import queue_steer_for_resume, resume_provider_session


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
is_resume = "resume" in args
for index, arg in enumerate(args):
    if arg == "-C":
        workspace = Path(args[index + 1])
    if arg == "--output-last-message":
        final_path = Path(args[index + 1])

(workspace / ("codex-resume-args.json" if is_resume else "codex-args.json")).write_text(json.dumps(args))
(workspace / "README.md").write_text("# Sample\\n\\nUpdated by fake Codex resume.\\n" if is_resume else "# Sample\\n\\nUpdated by fake Codex.\\n")
if final_path is not None:
    final_path.write_text(f"Fake Codex resumed: {args[-1]}" if is_resume else "Fake Codex completed the task.")

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
        worker = CodexWorker(
            codex_executable=str(fake_codex),
            profile="test",
            model="openai/gpt-oss-120b:free",
            timeout_seconds=10,
        )
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
        assert run.provider_session_id == "fake"
        assert run.resume_command == "codex exec resume fake"
        codex_args = json.loads((workspace / "codex-args.json").read_text())
        assert codex_args[:2] == ["--profile", "test"]
        # The model flag is still passed (after the injected MCP -c overrides).
        model_index = codex_args.index("-m")
        assert codex_args[model_index + 1] == "openai/gpt-oss-120b:free"
        # The Workforce MCP server must be wired in so the agent gets assign()/report()/etc.
        assert any("mcp_servers.workforce.command" in arg for arg in codex_args)
        assert any("workforce_runtime" in arg and "mcp" in arg and "serve" in arg for arg in codex_args)
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
        assert "provider_session_registered" in event_types


def test_codex_worker_consumes_queued_steer_with_provider_session(tmp_path: Path) -> None:
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
            title="Update README with steering",
            objective="Update the README and accept a later steering message.",
            assign_to="codex_worker",
        )
        queued_event_id = queue_steer_for_resume(
            runtime,
            agent_id="codex_worker",
            task_id=task.task_id,
            message="Please add the steered change.",
        )
        worker = CodexWorker(
            codex_executable=str(fake_codex),
            profile="test",
            model="openai/gpt-oss-120b:free",
            timeout_seconds=10,
        )
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
        assert run.provider_session_id == "fake"
        resume_args = json.loads((workspace / "codex-resume-args.json").read_text())
        assert resume_args[:4] == ["--profile", "test", "-m", "openai/gpt-oss-120b:free"]
        assert resume_args[-2:] == ["fake", "Please add the steered change."]
        reports = runtime.store.list_reports_by_task(task.task_id)
        assert reports[0].summary == "Fake Codex resumed: Please add the steered change."

        events = runtime.store.list_events()
        consumed = [event for event in events if event.event_type == "human_agent_steer_consumed"]
        assert consumed[0].payload["queued_event_id"] == queued_event_id
        assert "provider_session_resume_finished" in [event.event_type for event in events]


def test_codex_worker_can_resume_idle_provider_session(tmp_path: Path) -> None:
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
            title="Update README then resume",
            objective="Update the README and keep a resumable session.",
            assign_to="codex_worker",
        )
        worker = CodexWorker(
            codex_executable=str(fake_codex),
            profile="test",
            model="openai/gpt-oss-120b:free",
            timeout_seconds=10,
        )
        worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        result = resume_provider_session(
            runtime,
            agent_id="codex_worker",
            task_id=task.task_id,
            message="Who am I continuing as?",
        )

        assert result.ok is True
        assert result.provider_session_id == "fake"
        assert result.final_text == "Fake Codex resumed: Who am I continuing as?"
        resume_args = json.loads((workspace / "codex-resume-args.json").read_text())
        assert resume_args[-2:] == ["fake", "Who am I continuing as?"]
