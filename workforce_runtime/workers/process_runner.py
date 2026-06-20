from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import FileStore


@dataclass(frozen=True)
class StreamedProcessResult:
    returncode: int
    stdout_path: Path
    stderr_path: Path
    timed_out: bool


def run_process_streaming(
    *,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    timeout_seconds: int | None,
    runtime: WorkforceRuntime,
    file_store: FileStore,
    run_id: str,
    task_id: str,
    agent_id: str,
    timeout_message: str,
    run_dir: Path | None = None,
) -> StreamedProcessResult:
    runtime.record_worker_run_started(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        executable=Path(command[0]).name if command else "",
    )

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    start = time.monotonic()
    timed_out = False

    while selector.get_map():
        if timeout_seconds is not None and not timed_out and time.monotonic() - start > timeout_seconds:
            timed_out = True
            process.kill()

        for key, _mask in selector.select(timeout=0.1):
            stream = str(key.data)
            data = os.read(key.fileobj.fileno(), 4096)
            if not data:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            if stream == "stdout":
                stdout_chunks.append(data)
            else:
                stderr_chunks.append(data)
            for text in _stream_text_chunks(data.decode(errors="replace")):
                runtime.record_worker_output(
                    run_id=run_id,
                    task_id=task_id,
                    actor_id=agent_id,
                    stream=stream,
                    text=text,
                )

        if process.poll() is not None and not selector.get_map():
            break

    raw_returncode = process.wait()
    returncode = -1 if timed_out else raw_returncode
    if timed_out and not stderr_chunks:
        stderr_chunks.append(timeout_message.encode())
        runtime.record_worker_output(
            run_id=run_id,
            task_id=task_id,
            actor_id=agent_id,
            stream="stderr",
            text=timeout_message,
        )

    stdout = b"".join(stdout_chunks).decode(errors="replace")
    stderr = b"".join(stderr_chunks).decode(errors="replace")
    stdout_path = file_store.save_worker_stdout(task_id, stdout, agent_id=agent_id, run_id=run_id)
    stderr_path = file_store.save_worker_stderr(task_id, stderr, agent_id=agent_id, run_id=run_id)
    if run_dir is not None:
        runtime.record_event(
            event_type="agent_run_path_registered",
            actor_id=agent_id,
            task_id=task_id,
            payload={"run_id": run_id, "run_dir": str(run_dir), "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)},
        )
    runtime.record_worker_run_finished(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        returncode=returncode,
        timed_out=timed_out,
    )

    return StreamedProcessResult(
        returncode=returncode,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timed_out=timed_out,
    )


def _stream_text_chunks(text: str) -> list[str]:
    if not text:
        return []
    parts: list[str] = []
    current = ""
    for char in text:
        current += char
        if char.isspace():
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    return parts
