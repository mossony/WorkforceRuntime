from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import FileStore

STREAM_EVENT_MAX_CHARS = 1200
STREAM_EVENT_PARTIAL_FLUSH_SECONDS = 1.0
SENTENCE_END_CHARS = ".!?。！？"


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

    start = time.monotonic()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stream_buffers = {"stdout": "", "stderr": ""}
    stream_updated_at = {"stdout": start, "stderr": start}
    timed_out = False

    def record_stream_text(stream: str, text: str) -> None:
        runtime.record_worker_output(
            run_id=run_id,
            task_id=task_id,
            actor_id=agent_id,
            stream=stream,
            text=text,
        )

    def flush_stream(stream: str, *, force: bool = False) -> None:
        chunks, remainder = _stream_text_flushes(stream_buffers[stream], force=force)
        stream_buffers[stream] = remainder
        if chunks:
            stream_updated_at[stream] = time.monotonic()
        for text in chunks:
            record_stream_text(stream, text)

    while selector.get_map():
        if timeout_seconds is not None and not timed_out and time.monotonic() - start > timeout_seconds:
            timed_out = True
            process.kill()

        ready = selector.select(timeout=0.1)
        for key, _mask in ready:
            stream = str(key.data)
            data = os.read(key.fileobj.fileno(), 4096)
            if not data:
                flush_stream(stream, force=True)
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            if stream == "stdout":
                stdout_chunks.append(data)
            else:
                stderr_chunks.append(data)
            stream_buffers[stream] += data.decode(errors="replace")
            stream_updated_at[stream] = time.monotonic()
            flush_stream(stream)

        now = time.monotonic()
        for stream in ("stdout", "stderr"):
            if stream_buffers[stream] and now - stream_updated_at[stream] >= STREAM_EVENT_PARTIAL_FLUSH_SECONDS:
                flush_stream(stream, force=True)

        if process.poll() is not None and not ready:
            for key in list(selector.get_map().values()):
                stream = str(key.data)
                flush_stream(stream, force=True)
                selector.unregister(key.fileobj)
                key.fileobj.close()
            break

        if process.poll() is not None and not selector.get_map():
            break

    raw_returncode = process.wait()
    returncode = -1 if timed_out else raw_returncode
    for stream in ("stdout", "stderr"):
        flush_stream(stream, force=True)
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


def _stream_text_flushes(text: str, *, force: bool = False) -> tuple[list[str], str]:
    if not text:
        return [], ""
    if force:
        return _split_long_stream_text(text), ""

    chunks: list[str] = []
    remainder = text
    while remainder:
        boundary = _stream_text_boundary(remainder)
        if boundary is None:
            break
        chunks.extend(_split_long_stream_text(remainder[:boundary]))
        remainder = remainder[boundary:]

    while len(remainder) >= STREAM_EVENT_MAX_CHARS:
        chunk, remainder = _take_long_stream_chunk(remainder)
        chunks.append(chunk)

    return [chunk for chunk in chunks if chunk], remainder


def _stream_text_boundary(text: str) -> int | None:
    for index, char in enumerate(text):
        if char == "\n":
            return index + 1
        if char in SENTENCE_END_CHARS:
            next_index = index + 1
            if next_index == len(text) or text[next_index].isspace():
                while next_index < len(text) and text[next_index].isspace() and text[next_index] != "\n":
                    next_index += 1
                return next_index
    return None


def _split_long_stream_text(text: str) -> list[str]:
    chunks: list[str] = []
    remainder = text
    while len(remainder) > STREAM_EVENT_MAX_CHARS:
        chunk, remainder = _take_long_stream_chunk(remainder)
        chunks.append(chunk)
    if remainder:
        chunks.append(remainder)
    return chunks


def _take_long_stream_chunk(text: str) -> tuple[str, str]:
    cut = text.rfind(" ", 0, STREAM_EVENT_MAX_CHARS)
    if cut < STREAM_EVENT_MAX_CHARS // 2:
        cut = STREAM_EVENT_MAX_CHARS
    else:
        cut += 1
    return text[:cut], text[cut:]
