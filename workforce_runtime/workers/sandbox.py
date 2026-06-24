from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workforce_runtime.config import load_runtime_config


FULL_ACCESS_MODE = "full_access"
SANDBOX_MODE = "sandbox"


@dataclass(frozen=True)
class SandboxApplication:
    command: list[str]
    applied: bool
    metadata: dict[str, object]


def worker_extra_args(worker_type: str, *, config: dict[str, Any] | None = None) -> list[str]:
    execution = _execution_config(config)
    if _execution_mode(execution) != SANDBOX_MODE:
        return []
    sandbox = _sandbox_config(execution)
    values = sandbox.get("worker_extra_args", {})
    if not isinstance(values, dict):
        return []
    args = values.get(worker_type, [])
    if not isinstance(args, list):
        return []
    return [str(item) for item in args]


def apply_process_sandbox(
    command: list[str],
    *,
    worker_type: str,
    workspace: Path,
    config: dict[str, Any] | None = None,
) -> SandboxApplication:
    execution = _execution_config(config)
    mode = _execution_mode(execution)
    if mode != SANDBOX_MODE:
        return SandboxApplication(
            command=list(command),
            applied=False,
            metadata={"execution_mode": mode, "sandbox_applied": False},
        )

    sandbox = _sandbox_config(execution)
    prefix = sandbox.get("command_prefix") or []
    if not isinstance(prefix, list) or not prefix:
        raise ValueError("execution.sandbox.command_prefix must be a non-empty list in sandbox mode")

    settings_path = str(sandbox.get("settings_path") or "")
    replacements = {
        "workspace": str(workspace),
        "settings_path": settings_path,
        "worker_type": worker_type,
    }
    expanded_prefix = [_expand_arg(str(item), replacements) for item in prefix]
    wrapped = [*expanded_prefix, *command]
    provider = str(sandbox.get("provider") or expanded_prefix[0])
    metadata = {
        "execution_mode": mode,
        "sandbox_applied": True,
        "sandbox_provider": provider,
        "sandbox_command_prefix": expanded_prefix,
        "sandbox_settings_path": settings_path,
        "worker_type": worker_type,
        "inner_command": command,
    }
    return SandboxApplication(command=wrapped, applied=True, metadata=metadata)


def record_sandbox_application(
    runtime: object,
    *,
    application: SandboxApplication,
    run_id: str,
    task_id: str,
    agent_id: str,
) -> None:
    if not application.applied:
        return
    record_event = getattr(runtime, "record_event")
    record_event(
        event_type="worker_sandbox_applied",
        actor_id=agent_id,
        task_id=task_id,
        payload={"run_id": run_id, **application.metadata},
    )


def _execution_config(config: dict[str, Any] | None) -> dict[str, Any]:
    runtime_config = config or load_runtime_config()
    execution = runtime_config.get("execution") or {}
    return execution if isinstance(execution, dict) else {}


def _execution_mode(execution: dict[str, Any]) -> str:
    mode = str(execution.get("mode") or FULL_ACCESS_MODE)
    return mode if mode in {FULL_ACCESS_MODE, SANDBOX_MODE} else FULL_ACCESS_MODE


def _sandbox_config(execution: dict[str, Any]) -> dict[str, Any]:
    sandbox = execution.get("sandbox") or {}
    return sandbox if isinstance(sandbox, dict) else {}


def _expand_arg(value: str, replacements: dict[str, str]) -> str:
    expanded = value
    for key, replacement in replacements.items():
        expanded = expanded.replace("{" + key + "}", replacement)
    return expanded
