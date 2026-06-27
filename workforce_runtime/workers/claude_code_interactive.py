from __future__ import annotations

import os
import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from threading import Lock
from uuid import uuid4

try:
    import pexpect
except ImportError:  # pragma: no cover - dependency guard for clearer runtime errors.
    pexpect = None  # type: ignore[assignment]

from workforce_runtime.config import load_runtime_config
from workforce_runtime.core import Artifact, ReportContract, TaskContract, UsageCost
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.base import RuntimeContext, WorkerRun
from workforce_runtime.workers.process_runner import _stream_text_flushes
from workforce_runtime.workers.sandbox import apply_process_sandbox, record_sandbox_application, worker_extra_args
from workforce_runtime.workers.steering import STEERABLE_SESSIONS


DONE_MARKER = "WORKFORCE_TASK_DONE"
PROMPT_END_MARKER = "WORKFORCE_INITIAL_PROMPT_END"
ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\))")


class ClaudeCodeInteractiveWorker:
    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_seconds: int | None = None,
        idle_finish_seconds: float | None = None,
        done_marker: str = DONE_MARKER,
        env: dict[str, str] | None = None,
    ) -> None:
        config = load_runtime_config().get("workers", {}).get("claude_code_interactive", {})
        configured_command = config.get("command")
        if command is not None:
            self.command = command
        elif isinstance(configured_command, list) and configured_command:
            self.command = [str(item) for item in configured_command]
        else:
            executable = str(config.get("executable") or "ccr")
            args = config.get("args") if isinstance(config.get("args"), list) else ["code"]
            self.command = [executable, *[str(item) for item in args]]
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else config.get("timeout_seconds", 900)
        self.idle_finish_seconds = (
            idle_finish_seconds if idle_finish_seconds is not None else float(config.get("idle_finish_seconds", 2.0))
        )
        self.input_submit_delay_seconds = float(config.get("input_submit_delay_seconds", 0.35))
        self.steer_interrupt_seconds = float(config.get("steer_interrupt_seconds", 0.8))
        self.done_marker = done_marker
        self.env = env
        self._runs: dict[str, WorkerRun] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def declare_capabilities(self) -> list[str]:
        return ["claude_code_interactive", "code_editing", "test_execution", "human_steering", "reporting"]

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        if pexpect is None:
            raise RuntimeError("pexpect is required for ClaudeCodeInteractiveWorker")

        run_id = f"run_{uuid4().hex[:12]}"
        file_store = FileStore(runtime_context.workspace)
        task_dir = file_store.agent_task_run_dir(
            agent_id=runtime_context.agent_id,
            task_id=task.task_id,
            run_id=run_id,
        )
        task_contract_path = task_dir / "task_contract.json"
        stdout_path = task_dir / "stdout.log"
        stderr_path = task_dir / "stderr.log"
        final_message_path = task_dir / "claude-interactive-final.md"
        task_contract_path.write_text(task.model_dump_json(indent=2))
        stderr_path.write_text("")
        runtime_context.runtime.materialize_agent_skills(
            agent_id=runtime_context.agent_id,
            worker_type="claude_code_interactive",
            workspace=runtime_context.workspace.resolve(),
            task_id=task.task_id,
            run_id=run_id,
            actor_id=runtime_context.agent_id,
        )

        prompt_path = task_dir / "prompt.md"
        prompt = self._build_prompt(task, runtime_context)
        prompt_path.write_text(prompt)
        terminal_prompt = self._build_terminal_prompt(prompt_path)
        command = [self.command[0], *worker_extra_args("claude_code_interactive"), *self.command[1:]]
        sandboxed = apply_process_sandbox(command, worker_type="claude_code_interactive", workspace=runtime_context.workspace)
        runtime_context.runtime.update_task_status(task.task_id, status="in_progress", actor_id=runtime_context.agent_id)
        runtime_context.runtime.record_worker_run_started(
            run_id=run_id,
            task_id=task.task_id,
            actor_id=runtime_context.agent_id,
            executable=" ".join(shlex.quote(item) for item in sandboxed.command),
        )
        record_sandbox_application(
            runtime_context.runtime,
            application=sandboxed,
            run_id=run_id,
            task_id=task.task_id,
            agent_id=runtime_context.agent_id,
        )
        runtime_context.runtime.record_event(
            event_type="agent_run_path_registered",
            actor_id=runtime_context.agent_id,
            task_id=task.task_id,
            payload={"run_id": run_id, "run_dir": str(task_dir), "stdout_path": str(stdout_path), "stderr_path": str(stderr_path), "prompt_path": str(prompt_path)},
        )

        env = os.environ.copy()
        env.update(self.env or {})
        session = _ClaudePtySession(
            command=sandboxed.command,
            cwd=runtime_context.workspace,
            env=env,
            run_id=run_id,
            task_id=task.task_id,
            agent_id=runtime_context.agent_id,
            runtime_context=runtime_context,
            stdout_path=stdout_path,
            done_marker=self.done_marker,
            timeout_seconds=self.timeout_seconds,
            idle_finish_seconds=self.idle_finish_seconds,
            input_submit_delay_seconds=self.input_submit_delay_seconds,
            steer_interrupt_seconds=self.steer_interrupt_seconds,
        )

        STEERABLE_SESSIONS.register(session)
        returncode = 1
        final_text = ""
        try:
            final_text, returncode = session.run(terminal_prompt)
        finally:
            STEERABLE_SESSIONS.unregister(agent_id=runtime_context.agent_id, task_id=task.task_id)

        final_message_path.write_text(final_text)
        diff_path = self._capture_git_diff(file_store, runtime_context.workspace, task.task_id)
        usage = {"input_tokens": 0, "output_tokens": 0}
        self._usage[run_id] = usage

        final_status = "completed" if returncode == 0 and final_text.strip() else "failed"
        runtime_context.runtime.register_artifact(
            Artifact(
                artifact_id=f"artifact_{uuid4().hex[:12]}",
                task_id=task.task_id,
                agent_id=runtime_context.agent_id,
                type="claude_interactive_final_message",
                path=str(final_message_path),
                description="Final Claude Code interactive worker message.",
            )
        )
        if diff_path is not None:
            runtime_context.runtime.register_artifact(
                Artifact(
                    artifact_id=f"artifact_{uuid4().hex[:12]}",
                    task_id=task.task_id,
                    agent_id=runtime_context.agent_id,
                    type="git_diff",
                    path=str(diff_path),
                    description="Git diff after Claude Code interactive worker run.",
                )
            )
        runtime_context.runtime.register_report(
            ReportContract(
                report_id=f"report_{uuid4().hex[:12]}",
                from_agent_id=runtime_context.agent_id,
                to_agent_id=runtime_context.manager_id or "human",
                task_id=task.task_id,
                summary=final_text.strip() or "Claude Code interactive worker did not produce a final message.",
                status=final_status,
                work_done=["Ran Claude Code interactive worker", "Captured live output and human steer messages"],
                evidence=[{"type": "stdout", "path": str(stdout_path)}, {"type": "stderr", "path": str(stderr_path)}],
                risks=[] if returncode == 0 else ["Claude interactive session exited without the done marker."],
                blockers=[],
                confidence=0.75 if returncode == 0 else 0.2,
                cost=UsageCost(tokens_used=0, runtime_seconds=0, tool_calls=0),
                next_action="Ready for manager review." if final_status == "completed" else "Inspect Claude interactive logs.",
                requires_decision=False,
                alignment_check="Claude Code interactive session received the structured task contract.",
            )
        )
        runtime_context.runtime.update_task_status(task.task_id, status=final_status, actor_id=runtime_context.agent_id)
        runtime_context.runtime.record_worker_run_finished(
            run_id=run_id,
            task_id=task.task_id,
            actor_id=runtime_context.agent_id,
            returncode=returncode,
            timed_out=returncode == -1,
        )

        run = WorkerRun(
            run_id=run_id,
            task_id=task.task_id,
            returncode=returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            task_contract_path=task_contract_path,
        )
        self._runs[run_id] = run
        return run

    def collect_artifacts(self, run_id: str) -> list[Path]:
        run = self._runs[run_id]
        paths = list(run.stdout_path.parent.iterdir())
        legacy_dir = FileStore(FileStore.workspace_from_run_file(run.stdout_path)).task_artifact_dir(run.task_id)
        if legacy_dir.exists():
            paths.extend(legacy_dir.iterdir())
        return sorted(set(paths))

    def stop_task(self, run_id: str) -> None:
        if run_id not in self._runs:
            raise KeyError(f"run not found: {run_id}")

    def get_usage(self, run_id: str) -> dict[str, int]:
        return self._usage[run_id]

    def _build_prompt(self, task: TaskContract, runtime_context: RuntimeContext) -> str:
        task_json = json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2)
        return f"""You are an interactive Claude Code worker inside Workforce Runtime.

Your agent id is: {runtime_context.agent_id}
Your manager is: {runtime_context.manager_id or "human"}
Your assigned task is:

{task_json}

Work only within the current workspace. Stream concise progress as you work.
The human operator may send steering messages while you are working; incorporate the latest steering message.
If any human steering message arrives, explicitly mention it in your final report and in any task result artifact that summarizes the work.
When the task is complete, provide a concise final report and include the exact marker {self.done_marker} on its own line.
{PROMPT_END_MARKER}
"""

    def _build_terminal_prompt(self, prompt_path: Path) -> str:
        return (
            f"Read the Workforce Runtime task instructions from {prompt_path} and complete that task. "
            f"Incorporate any live human steering messages. Finish with {self.done_marker} on its own line."
        )

    def _capture_git_diff(self, file_store: FileStore, workspace: Path, task_id: str) -> Path | None:
        result = subprocess.run(["git", "diff", "--"], cwd=workspace, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return file_store.save_git_diff(task_id, result.stdout)


class _ClaudePtySession:
    def __init__(
        self,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        run_id: str,
        task_id: str,
        agent_id: str,
        runtime_context: RuntimeContext,
        stdout_path: Path,
        done_marker: str,
        timeout_seconds: int | None,
        idle_finish_seconds: float,
        input_submit_delay_seconds: float,
        steer_interrupt_seconds: float,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.run_id = run_id
        self.task_id = task_id
        self.agent_id = agent_id
        self.runtime_context = runtime_context
        self.stdout_path = stdout_path
        self.done_marker = done_marker
        self.timeout_seconds = timeout_seconds
        self.idle_finish_seconds = idle_finish_seconds
        self.input_submit_delay_seconds = input_submit_delay_seconds
        self.steer_interrupt_seconds = steer_interrupt_seconds
        self._lock = Lock()
        self._child = None
        self._prompt_sent = False
        self._queued_steers: list[tuple[str, str]] = []

    def run(self, prompt: str) -> tuple[str, int]:
        assert pexpect is not None
        child = pexpect.spawn(
            self.command[0],
            self.command[1:],
            cwd=str(self.cwd),
            env=self.env,
            encoding="utf-8",
            codec_errors="replace",
            timeout=0.2,
        )
        self._child = child
        child.linesep = "\r"
        try:
            child.setecho(False)
        except Exception:
            pass
        transcript = ""
        pending = ""
        saw_done = False
        last_output = time.monotonic()
        start = last_output
        startup_text = self._drain_startup(child)
        if startup_text:
            transcript += startup_text
            self.stdout_path.write_text(transcript)
        self._send_user_message(child, prompt)
        self._mark_prompt_sent_and_flush_steers()

        try:
            while True:
                if self.timeout_seconds is not None and time.monotonic() - start > self.timeout_seconds:
                    self._record_output("status", "Claude interactive worker timed out.")
                    self._terminate()
                    return _final_text(transcript, self.done_marker), -1
                try:
                    data = child.read_nonblocking(size=4096, timeout=0.2)
                except pexpect.TIMEOUT:
                    if saw_done and time.monotonic() - last_output >= self.idle_finish_seconds:
                        self._terminate()
                        return _final_text(transcript, self.done_marker), 0
                    continue
                except pexpect.EOF:
                    return _final_text(transcript, self.done_marker), 0 if saw_done else 1
                text = _clean_terminal_text(data)
                if not text:
                    continue
                transcript += text
                pending += text
                last_output = time.monotonic()
                for chunk in _flush_output_chunks(pending):
                    self._record_output("terminal", chunk)
                    pending = pending[len(chunk) :]
                self.stdout_path.write_text(transcript)
                if _has_assistant_done_marker(transcript, self.done_marker):
                    saw_done = True
                    for chunk, remainder in [_stream_text_flushes(pending, force=True)]:
                        for item in chunk:
                            self._record_output("terminal", item)
                        pending = remainder
        finally:
            self.stdout_path.write_text(transcript)
            self._terminate()

    def _drain_startup(self, child: pexpect.spawn, *, max_seconds: float = 15.0) -> str:
        transcript = ""
        pending = ""
        started_at = time.monotonic()
        last_output = started_at
        selected_trust = False
        selected_bypass_permissions = False
        while time.monotonic() - started_at < max_seconds:
            try:
                data = child.read_nonblocking(size=4096, timeout=0.2)
            except pexpect.TIMEOUT:
                if transcript and time.monotonic() - last_output >= 1.0:
                    break
                if not transcript and time.monotonic() - started_at >= 2.0:
                    break
                continue
            except pexpect.EOF:
                break
            text = _clean_terminal_text(data)
            if not text:
                continue
            transcript += text
            pending += text
            last_output = time.monotonic()
            for chunk in _flush_output_chunks(pending):
                self._record_output("terminal", chunk)
                pending = pending[len(chunk) :]
            if not selected_bypass_permissions and _looks_like_bypass_permissions_prompt(transcript):
                child.sendline("2")
                selected_bypass_permissions = True
                self._record_output("status", "Accepted Claude Code bypass-permissions prompt.")
                last_output = time.monotonic()
            elif not selected_trust and _looks_like_trust_folder_prompt(transcript):
                child.sendline("1")
                selected_trust = True
                self._record_output("status", "Accepted Claude Code workspace trust prompt.")
                last_output = time.monotonic()
        for chunk, remainder in [_stream_text_flushes(pending, force=True)]:
            for item in chunk:
                self._record_output("terminal", item)
            pending = remainder
        return transcript

    def steer(self, message: str, *, from_agent_id: str = "human") -> None:
        with WorkforceRuntime(self.runtime_context.db_path) as runtime:
            runtime.record_event(
                event_type="human_agent_steer_sent",
                actor_id=from_agent_id,
                task_id=self.task_id,
                payload={"target_agent_id": self.agent_id, "run_id": self.run_id, "message": message},
            )
            runtime.record_worker_output(
                run_id=self.run_id,
                task_id=self.task_id,
                actor_id=self.agent_id,
                stream="status",
                text="Human steering accepted for delivery to Claude Code.",
            )
            runtime.send_discussion_message(
                from_agent_id=from_agent_id,
                to_agent_id=self.agent_id,
                task_id=self.task_id,
                message=message,
                thread_id=self.run_id,
            )
        with self._lock:
            if self._child is not None and self._child.isalive() and self._prompt_sent:
                self._send_steering_message(self._child, f"Human steering message from {from_agent_id}: {message}")
            else:
                self._queued_steers.append((from_agent_id, message))

    def interrupt(self, *, from_agent_id: str = "human") -> None:
        with WorkforceRuntime(self.runtime_context.db_path) as runtime:
            runtime.record_event(
                event_type="human_agent_interrupt_sent",
                actor_id=from_agent_id,
                task_id=self.task_id,
                payload={"target_agent_id": self.agent_id, "run_id": self.run_id},
            )
        with self._lock:
            if self._child is not None and self._child.isalive():
                self._child.sendcontrol("c")

    def _record_output(self, stream: str, text: str) -> None:
        if not _should_record_output(stream, text):
            return
        self.runtime_context.runtime.record_worker_output(
            run_id=self.run_id,
            task_id=self.task_id,
            actor_id=self.agent_id,
            stream=stream,
            text=text,
        )

    def _terminate(self) -> None:
        with self._lock:
            if self._child is None or not self._child.isalive():
                return
            try:
                self._child.sendcontrol("c")
                self._child.close(force=True)
            except Exception:
                pass

    def _mark_prompt_sent_and_flush_steers(self) -> None:
        with self._lock:
            self._prompt_sent = True
            if self._child is None or not self._child.isalive():
                return
            queued = list(self._queued_steers)
            self._queued_steers.clear()
            for from_agent_id, message in queued:
                self._send_user_message(self._child, f"Human steering message from {from_agent_id}: {message}")

    def _send_user_message(self, child: pexpect.spawn, message: str) -> None:
        child.send(message)
        time.sleep(self.input_submit_delay_seconds)
        child.send("\r")

    def _send_steering_message(self, child: pexpect.spawn, message: str) -> None:
        if self.steer_interrupt_seconds > 0:
            child.send("\x1b")
            time.sleep(self.steer_interrupt_seconds)
        self._send_user_message(child, message)


def _clean_terminal_text(text: str) -> str:
    cleaned = ANSI_RE.sub("", text)
    cleaned = cleaned.replace("\r", "\n")
    return cleaned


def _flush_output_chunks(text: str) -> list[str]:
    chunks, _remainder = _stream_text_flushes(text)
    return chunks


def _looks_like_trust_folder_prompt(text: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    return "yesitrustthisfolder" in compact or ("trustthisfolder" in compact and "securityguide" in compact)


def _looks_like_bypass_permissions_prompt(text: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    return "bypasspermissions" in compact and ("yesiaccept" in compact or "acceptallresponsibility" in compact)


def _looks_like_assistant_output(text: str) -> bool:
    return "⏺" in text or "WORKFORCE_ASSISTANT_OUTPUT" in text


def _has_assistant_done_marker(text: str, marker: str) -> bool:
    marker_index = text.rfind(marker)
    if marker_index < 0:
        return False
    assistant_index = max(
        text.rfind("⏺", 0, marker_index),
        text.rfind("WORKFORCE_ASSISTANT_OUTPUT", 0, marker_index),
    )
    if assistant_index < 0:
        return False
    latest_input_index = max(
        text.rfind("Human steering message", 0, marker_index),
        text.rfind("Humansteeringmessage", 0, marker_index),
        text.rfind("Read the Workforce Runtime task instructions", 0, marker_index),
        text.rfind("ReadtheWorkforceRuntimetaskinstructions", 0, marker_index),
    )
    return latest_input_index < assistant_index


def _should_record_output(stream: str, text: str) -> bool:
    if stream != "terminal":
        return bool(text.strip())
    stripped = text.strip()
    if not stripped:
        return False
    compact = re.sub(r"\s+", "", stripped.lower())
    if len(compact) <= 2 and "⏺" not in stripped:
        return False
    chrome_terms = (
        "bypasspermissionson",
        "shift+tabtocycle",
        "esctointerrupt",
        "ctrl+gtoedit",
        "tipsforgettingstarted",
        "welcomeback",
        "release-notes",
        "apiusagebilling",
        "claudecodev",
        "completeworkforcetaskwithsteering",
        "beboppin",
        "tokens",
        "thoughtfor",
        "usetip",
    )
    if any(term in compact for term in chrome_terms):
        return False
    border_chars = set("─│╭╮╰╯┌┐└┘├┤┬┴┼═║╔╗╚╝")
    if all(char in border_chars for char in stripped):
        return False
    return True


def _final_text(transcript: str, marker: str) -> str:
    cleaned = transcript.strip()
    if marker in cleaned:
        cleaned = cleaned.rsplit(marker, 1)[0].strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    meaningful_lines = [line for line in lines if not _looks_like_final_text_noise(line)]
    return "\n".join(meaningful_lines[-20:])


def _looks_like_final_text_noise(line: str) -> bool:
    if not _should_record_output("terminal", line):
        return True
    compact = re.sub(r"[^a-z0-9_]+", "", line.lower())
    noisy_terms = (
        "readtheworkforceruntimetaskinstructions",
        "workforce_initial_prompt_end",
        "workforceinitialpromptend",
        "humansteeringmessagefrom",
        "finishwithworkforce_task_done",
        "finishwithworkforcetaskdone",
        "pasteagaintoexpand",
    )
    return any(term in compact for term in noisy_terms)
