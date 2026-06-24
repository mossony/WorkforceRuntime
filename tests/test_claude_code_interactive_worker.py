from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import ClaudeCodeInteractiveWorker, RuntimeContext
from workforce_runtime.workers.claude_code_interactive import _final_text, _has_assistant_done_marker
from workforce_runtime.workers.steering import STEERABLE_SESSIONS


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def write_fake_interactive_claude(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import select
import sys
import time
from pathlib import Path

print("Fake Claude ready", flush=True)
print("❯ 1. Yes, I trust this folder", flush=True)
print("  2. No, exit", flush=True)
while True:
    answer = sys.stdin.readline()
    if answer.strip() == "1":
        break
print("WARNING: Claude Code running in Bypass Permissions mode", flush=True)
print("❯ 1. No, exit", flush=True)
print("  2. Yes, I accept", flush=True)
while True:
    answer = sys.stdin.readline()
    if answer.strip() == "2":
        break
print("Ready for task", flush=True)
while True:
    initial = sys.stdin.readline()
    if not initial:
        continue
    if "WORKFORCE_INITIAL_PROMPT_END" in initial or "Read the Workforce Runtime task instructions" in initial:
        break
print("Working on task.", flush=True)
deadline = time.time() + 5
while time.time() < deadline:
    ready, _, _ = select.select([sys.stdin], [], [], 0.1)
    if not ready:
        print("Still working.", flush=True)
        time.sleep(0.1)
        continue
    message = sys.stdin.readline()
    print("Received steer: " + message.strip(), flush=True)
    Path("README.md").write_text("# Sample\\n\\nSteered by fake interactive Claude.\\n")
    print("⏺ Final report: completed after human steering.", flush=True)
    print("WORKFORCE_TASK_DONE", flush=True)
    break
"""
    )
    path.chmod(path.stat().st_mode | 0o111)


def test_claude_code_interactive_worker_can_be_steered(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    (workspace / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)

    fake_claude = tmp_path / "fake_interactive_claude.py"
    write_fake_interactive_claude(fake_claude)

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(
            title="Update README interactively",
            objective="Update README after receiving human steering.",
            assign_to="claude_worker",
        )
    worker = ClaudeCodeInteractiveWorker(command=[str(fake_claude)], timeout_seconds=10, idle_finish_seconds=0.2)
    result_holder: dict[str, object] = {}

    def run_worker() -> None:
        with WorkforceRuntime(db_path) as worker_runtime:
            result_holder["run"] = worker.start_task(
                task,
                RuntimeContext(
                    runtime=worker_runtime,
                    db_path=db_path,
                    workspace=workspace,
                    agent_id="claude_worker",
                    manager_id="engineering_manager",
                ),
            )

    thread = threading.Thread(target=run_worker)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if STEERABLE_SESSIONS.find(agent_id="claude_worker", task_id=task.task_id) is not None:
            break
        time.sleep(0.05)
    steer = STEERABLE_SESSIONS.steer(
        agent_id="claude_worker",
        task_id=task.task_id,
        from_agent_id="human",
        message="Please update README now and finish.",
    )
    assert steer.ok
    thread.join(timeout=10)
    assert not thread.is_alive()

    run = result_holder["run"]
    assert getattr(run, "returncode") == 0
    assert "Steered by fake interactive Claude" in (workspace / "README.md").read_text()
    with WorkforceRuntime(db_path) as runtime:
        reports = runtime.store.list_reports_by_task(task.task_id)
        assert len(reports) == 1
        assert reports[0].status == "completed"
        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "human_agent_steer_sent" in event_types
        assert "discussion_message" in event_types
        assert "worker_output" in event_types
        assert "worker_run_finished" in event_types


def test_claude_interactive_done_marker_requires_assistant_output() -> None:
    marker = "WORKFORCE_TASK_DONE"
    prompt_echo = f"Read the Workforce Runtime task instructions. Finish with {marker} on its own line."
    assert not _has_assistant_done_marker(prompt_echo, marker)

    steering_echo = prompt_echo + f"\nHuman steering message from human: finish with {marker}."
    assert not _has_assistant_done_marker(steering_echo, marker)

    assistant_done = steering_echo + f"\n⏺ Completed the requested change.\n{marker}\n"
    assert _has_assistant_done_marker(assistant_done, marker)
    assert _final_text(assistant_done, marker) == "⏺ Completed the requested change."
