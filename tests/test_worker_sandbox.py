from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import ClaudeCodeWorker, CodexWorker, RuntimeContext
from workforce_runtime.workers.sandbox import apply_process_sandbox


EXAMPLE_ORG = Path(__file__).resolve().parents[1] / "examples/simple_engineering_org/org.yaml"


def test_process_sandbox_wraps_command_only_in_sandbox_mode(tmp_path: Path) -> None:
    command = ["codex", "exec", "hello"]
    full_access = apply_process_sandbox(
        command,
        worker_type="codex",
        workspace=tmp_path,
        config={"execution": {"mode": "full_access"}},
    )
    assert full_access.command == command
    assert full_access.applied is False

    sandboxed = apply_process_sandbox(
        command,
        worker_type="codex",
        workspace=tmp_path,
        config={
            "execution": {
                "mode": "sandbox",
                "sandbox": {
                    "provider": "test",
                    "command_prefix": ["srt", "--settings", "{settings_path}", "--workspace", "{workspace}"],
                    "settings_path": "settings.json",
                },
            }
        },
    )
    assert sandboxed.applied is True
    assert sandboxed.command == ["srt", "--settings", "settings.json", "--workspace", str(tmp_path), *command]


def test_codex_worker_uses_configured_process_sandbox(tmp_path: Path, monkeypatch) -> None:
    workspace = _git_workspace(tmp_path)
    fake_codex = tmp_path / "fake_codex.py"
    fake_sandbox = tmp_path / "fake_sandbox.py"
    sandbox_log = tmp_path / "sandbox-log.json"
    _write_fake_codex(fake_codex)
    _write_fake_sandbox(fake_sandbox)
    _write_sandbox_config(tmp_path, fake_sandbox)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SANDBOX_LOG", str(sandbox_log))

    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Update README", objective="Update README.", assign_to="codex_worker")
        worker = CodexWorker(codex_executable=str(fake_codex), profile="test", timeout_seconds=10)
        run = worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=tmp_path / "runtime.sqlite",
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        assert run.returncode == 0
        wrapped = json.loads(sandbox_log.read_text())
        assert wrapped[:2] == ["--settings", str(tmp_path / "srt-settings.json")]
        assert wrapped[2] == str(fake_codex)
        assert "--dangerously-bypass-approvals-and-sandbox" in wrapped
        event = next(item for item in runtime.store.list_events() if item.event_type == "worker_sandbox_applied")
        assert event.payload["sandbox_applied"] is True
        assert event.payload["sandbox_provider"] == "test_sandbox"


def test_claude_worker_uses_configured_process_sandbox(tmp_path: Path, monkeypatch) -> None:
    workspace = _git_workspace(tmp_path)
    fake_claude = tmp_path / "fake_claude.py"
    fake_sandbox = tmp_path / "fake_sandbox.py"
    sandbox_log = tmp_path / "sandbox-log.json"
    _write_fake_claude(fake_claude)
    _write_fake_sandbox(fake_sandbox)
    _write_sandbox_config(tmp_path, fake_sandbox)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SANDBOX_LOG", str(sandbox_log))

    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Update README", objective="Update README.", assign_to="claude_worker")
        worker = ClaudeCodeWorker(claude_executable=str(fake_claude), timeout_seconds=10)
        run = worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=tmp_path / "runtime.sqlite",
                workspace=workspace,
                agent_id="claude_worker",
                manager_id="engineering_manager",
            ),
        )

        assert run.returncode == 0
        wrapped = json.loads(sandbox_log.read_text())
        assert wrapped[:2] == ["--settings", str(tmp_path / "srt-settings.json")]
        assert wrapped[2] == str(fake_claude)
        assert "--dangerously-skip-permissions" in wrapped
        assert any(item.event_type == "worker_sandbox_applied" for item in runtime.store.list_events())


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    (workspace / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)
    return workspace


def _write_sandbox_config(tmp_path: Path, fake_sandbox: Path) -> None:
    settings_path = tmp_path / "srt-settings.json"
    settings_path.write_text("{}")
    (tmp_path / "workforce_runtime_config.json").write_text(
        json.dumps(
            {
                "execution": {
                    "mode": "sandbox",
                    "sandbox": {
                        "provider": "test_sandbox",
                        "command_prefix": [str(fake_sandbox), "--settings", "{settings_path}"],
                        "settings_path": str(settings_path),
                        "worker_extra_args": {
                            "codex": ["--dangerously-bypass-approvals-and-sandbox"],
                            "claude_code": ["--dangerously-skip-permissions"],
                        },
                    },
                }
            }
        )
    )


def _write_fake_sandbox(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys

args = sys.argv[1:]
with open(os.environ["SANDBOX_LOG"], "w") as handle:
    handle.write(json.dumps(args))
if args[:1] == ["--settings"]:
    args = args[2:]
result = subprocess.run(args)
raise SystemExit(result.returncode)
"""
    )
    path.chmod(path.stat().st_mode | 0o111)


def _write_fake_codex(path: Path) -> None:
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
workspace.mkdir(parents=True, exist_ok=True)
(workspace / "README.md").write_text("# Sample\\n\\nUpdated in sandbox.\\n")
if final_path is not None:
    final_path.write_text("Sandboxed Codex completed the task.")
print(json.dumps({"type": "thread.started", "thread_id": "fake"}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}}))
"""
    )
    path.chmod(path.stat().st_mode | 0o111)


def _write_fake_claude(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

Path("README.md").write_text("# Sample\\n\\nUpdated in sandbox.\\n")
print(json.dumps({"result": "Sandboxed Claude completed the task.", "session_id": "claude_fake", "usage": {"input_tokens": 1, "output_tokens": 2}}))
"""
    )
    path.chmod(path.stat().st_mode | 0o111)
