from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from workforce_runtime.config import load_runtime_config
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.process_runner import run_process_streaming
from workforce_runtime.workers.sandbox import apply_process_sandbox, record_sandbox_application, worker_extra_args


@dataclass(frozen=True)
class ProviderSession:
    provider: str
    provider_session_id: str
    agent_id: str
    task_id: str
    run_id: str
    workspace: Path
    resume_command: str
    worker_type: str = ""
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ResumeResult:
    ok: bool
    status: str
    run_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    provider_session_id: str = ""
    final_text: str = ""
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    message: str = ""


def extract_codex_session_id(stdout_text: str) -> str:
    for line in stdout_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            return str(event.get("thread_id") or "")
    return ""


def extract_claude_session_id(stdout_text: str) -> str:
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("session_id") or "")


def codex_resume_command(
    *,
    executable: str,
    profile: str,
    model: str | None,
    approval_policy: str,
    sandbox_mode: str,
    workspace: Path,
    session_id: str,
    final_message_path: Path,
    message: str,
) -> list[str]:
    return [
        executable,
        *worker_extra_args("codex"),
        "--profile",
        profile,
        *(["-m", str(model)] if model else []),
        "-a",
        approval_policy,
        "-s",
        sandbox_mode,
        "-C",
        str(workspace),
        "exec",
        "resume",
        "--json",
        "--output-last-message",
        str(final_message_path),
        "--skip-git-repo-check",
        session_id,
        message,
    ]


def claude_resume_command(
    *,
    executable: str,
    session_id: str,
    message: str,
) -> list[str]:
    return [executable, *worker_extra_args("claude_code"), "-p", "--resume", session_id, "--output-format", "json", message]


def latest_provider_session(
    runtime: WorkforceRuntime,
    *,
    agent_id: str,
    task_id: str | None = None,
) -> ProviderSession | None:
    for event in reversed(runtime.store.list_events()):
        if event.event_type != "provider_session_registered":
            continue
        if event.actor_id != agent_id:
            continue
        if task_id and event.task_id != task_id:
            continue
        payload = event.payload
        session_id = str(payload.get("provider_session_id") or "")
        workspace = str(payload.get("workspace") or "")
        if not session_id or not workspace:
            continue
        return ProviderSession(
            provider=str(payload.get("provider") or ""),
            provider_session_id=session_id,
            agent_id=agent_id,
            task_id=event.task_id or "",
            run_id=str(payload.get("run_id") or ""),
            workspace=Path(workspace).resolve(),
            resume_command=str(payload.get("resume_command") or ""),
            worker_type=str(payload.get("worker_type") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )
    return None


def queue_steer_for_resume(
    runtime: WorkforceRuntime,
    *,
    agent_id: str,
    task_id: str | None,
    message: str,
    from_agent_id: str = "human",
) -> str:
    event = runtime.record_event(
        event_type="human_agent_steer_queued",
        actor_id=from_agent_id,
        task_id=task_id,
        payload={"target_agent_id": agent_id, "message": message},
    )
    return event.event_id


def consume_queued_steers_for_resume(
    runtime: WorkforceRuntime,
    *,
    agent_id: str,
    task_id: str,
    provider_session_id: str,
    workspace: Path,
    metadata: dict[str, object] | None = None,
) -> list[ResumeResult]:
    consumed_ids = {
        str(event.payload.get("queued_event_id") or "")
        for event in runtime.store.list_events()
        if event.event_type == "human_agent_steer_consumed"
    }
    queued = [
        event
        for event in runtime.store.list_events()
        if event.event_type == "human_agent_steer_queued"
        and event.event_id not in consumed_ids
        and event.task_id == task_id
        and event.payload.get("target_agent_id") == agent_id
    ]
    results: list[ResumeResult] = []
    for event in queued:
        message = str(event.payload.get("message") or "")
        result = resume_provider_session(
            runtime,
            agent_id=agent_id,
            message=message,
            task_id=task_id,
            from_agent_id=str(event.actor_id or "human"),
            session=ProviderSession(
                provider="",
                provider_session_id=provider_session_id,
                agent_id=agent_id,
                task_id=task_id,
                run_id="",
                workspace=workspace,
                resume_command="",
                metadata=metadata or {},
            ),
        )
        runtime.record_event(
            event_type="human_agent_steer_consumed",
            actor_id="runtime",
            task_id=task_id,
            payload={
                "target_agent_id": agent_id,
                "queued_event_id": event.event_id,
                "resume_run_id": result.run_id,
                "status": result.status,
            },
        )
        results.append(result)
    return results


def resume_provider_session(
    runtime: WorkforceRuntime,
    *,
    agent_id: str,
    message: str,
    task_id: str | None = None,
    from_agent_id: str = "human",
    session: ProviderSession | None = None,
) -> ResumeResult:
    session = session or latest_provider_session(runtime, agent_id=agent_id, task_id=task_id)
    if session is None:
        return ResumeResult(ok=False, status="no_provider_session", agent_id=agent_id, task_id=task_id or "", message=message)

    agent = runtime.get_agent(agent_id)
    worker_type = session.worker_type or (agent.worker_type if agent is not None else "")
    provider = session.provider or _provider_from_worker_type(worker_type)
    if provider not in {"codex", "claude_code"}:
        return ResumeResult(
            ok=False,
            status="unsupported_provider",
            agent_id=agent_id,
            task_id=task_id or session.task_id,
            provider_session_id=session.provider_session_id,
            message=message,
        )

    effective_task_id = task_id or session.task_id or f"conversation_{agent_id}"
    workspace = session.workspace.resolve()
    run_id = f"run_resume_{uuid4().hex[:12]}"
    file_store = FileStore(workspace)
    run_dir = file_store.agent_task_run_dir(agent_id=agent_id, task_id=effective_task_id, run_id=run_id)
    prompt_path = run_dir / "resume_prompt.txt"
    prompt_path.write_text(message)

    final_message_path = run_dir / ("codex-resume-final.md" if provider == "codex" else "claude-resume-final.md")
    if provider == "codex":
        config = load_runtime_config().get("workers", {}).get("codex", {})
        metadata = session.metadata or {}
        timeout_seconds = _int_or_none(metadata.get("timeout_seconds")) or _int_or_none(config.get("timeout_seconds"))
        command = codex_resume_command(
            executable=str(metadata.get("executable") or config.get("executable") or "codex"),
            profile=str(metadata.get("profile") or config.get("profile") or "workforce-openrouter"),
            model=metadata.get("model") or config.get("model"),
            approval_policy=str(metadata.get("approval_policy") or config.get("approval_policy") or "never"),
            sandbox_mode=str(metadata.get("sandbox_mode") or config.get("sandbox_mode") or "workspace-write"),
            workspace=workspace,
            session_id=session.provider_session_id,
            final_message_path=final_message_path,
            message=message,
        )
    else:
        config = load_runtime_config().get("workers", {}).get("claude_code", {})
        metadata = session.metadata or {}
        timeout_seconds = _int_or_none(metadata.get("timeout_seconds")) or _int_or_none(config.get("timeout_seconds"))
        command = claude_resume_command(
            executable=str(metadata.get("executable") or config.get("executable") or "claude"),
            session_id=session.provider_session_id,
            message=message,
        )

    sandboxed = apply_process_sandbox(command, worker_type=provider, workspace=workspace)
    record_sandbox_application(
        runtime,
        application=sandboxed,
        run_id=run_id,
        task_id=effective_task_id,
        agent_id=agent_id,
    )

    runtime.record_event(
        event_type="provider_session_resume_requested",
        actor_id=from_agent_id,
        task_id=effective_task_id,
        payload={
            "target_agent_id": agent_id,
            "provider": provider,
            "provider_session_id": session.provider_session_id,
            "message": message,
            "run_id": run_id,
            "execution_mode": sandboxed.metadata.get("execution_mode", "full_access"),
            "sandbox_applied": sandboxed.applied,
        },
    )
    streamed = run_process_streaming(
        command=sandboxed.command,
        cwd=workspace,
        env=None,
        timeout_seconds=timeout_seconds,
        runtime=runtime,
        file_store=file_store,
        run_id=run_id,
        task_id=effective_task_id,
        agent_id=agent_id,
        timeout_message=f"{provider} resume timed out",
        run_dir=run_dir,
    )
    final_text = _resume_final_text(provider=provider, stdout_path=streamed.stdout_path, final_message_path=final_message_path)
    new_session_id = _resume_session_id(provider=provider, stdout_path=streamed.stdout_path) or session.provider_session_id
    if new_session_id:
        runtime.record_provider_session(
            provider=provider,
            provider_session_id=new_session_id,
            run_id=run_id,
            task_id=effective_task_id,
            actor_id=agent_id,
            workspace=str(workspace),
            resume_command=_display_resume_command(provider, new_session_id),
            worker_type=worker_type,
            metadata=session.metadata or {},
        )
    runtime.record_event(
        event_type="provider_session_resume_finished",
        actor_id=agent_id,
        task_id=effective_task_id,
        payload={
            "provider": provider,
            "provider_session_id": new_session_id,
            "run_id": run_id,
            "returncode": streamed.returncode,
            "final_text": final_text[:1000],
        },
    )
    return ResumeResult(
        ok=streamed.returncode == 0,
        status="completed" if streamed.returncode == 0 else "failed",
        run_id=run_id,
        task_id=effective_task_id,
        agent_id=agent_id,
        provider_session_id=new_session_id,
        final_text=final_text,
        stdout_path=streamed.stdout_path,
        stderr_path=streamed.stderr_path,
        message=message,
    )


def _provider_from_worker_type(worker_type: str) -> str:
    if "codex" in worker_type:
        return "codex"
    if "claude" in worker_type:
        return "claude_code"
    return worker_type


def _resume_final_text(*, provider: str, stdout_path: Path, final_message_path: Path) -> str:
    if provider == "codex":
        return final_message_path.read_text() if final_message_path.exists() else ""
    text = stdout_path.read_text()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(payload.get("result") or payload.get("content") or payload.get("text") or "")


def _resume_session_id(*, provider: str, stdout_path: Path) -> str:
    text = stdout_path.read_text()
    return extract_codex_session_id(text) if provider == "codex" else extract_claude_session_id(text)


def _display_resume_command(provider: str, session_id: str) -> str:
    if provider == "codex":
        return f"codex exec resume {session_id}"
    return f"claude -p --resume {session_id}"


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def git_diff_path_after_resume(file_store: FileStore, workspace: Path, task_id: str) -> Path | None:
    result = subprocess.run(["git", "diff", "--"], cwd=workspace, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return file_store.save_git_diff(task_id, result.stdout)
