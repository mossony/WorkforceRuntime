from __future__ import annotations

import os
from typing import Any

from workforce_runtime.config.model_registry import model_capabilities
from workforce_runtime.config.runtime_config import load_runtime_config
from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.core.permissions import DELEGATE_TASK


DEFAULT_UNAVAILABLE_MODEL_ERROR_FRAGMENTS = (
    "model not found",
    "model metadata",
    "no endpoints",
    "no endpoint",
    "invalid model",
    "unknown model",
    "does not exist",
    "not found",
    "dne",
    "degraded function cannot be invoked",
    "function cannot be invoked",
)


def is_unavailable_model_error(error: object) -> bool:
    message = str(error or "").lower()
    if not message:
        return False
    fragments = _failover_config().get("unavailable_error_fragments") or DEFAULT_UNAVAILABLE_MODEL_ERROR_FRAGMENTS
    return any(str(fragment).lower() in message for fragment in fragments)


def choose_agent_replacement_model(
    agent: AgentProfile,
    *,
    failed_model: str = "",
    runtime_config: dict[str, Any] | None = None,
) -> str:
    config = runtime_config or load_runtime_config()
    failover = dict(config.get("model_failover") or {})
    pool_key = "management_fallback_models" if is_management_agent(agent) else "worker_fallback_models"
    candidates = [str(model) for model in failover.get(pool_key) or []]
    if not candidates:
        large_task = dict(config.get("demos", {}).get("large_task_100") or {})
        candidates = [str(model) for model in large_task.get("management_models" if is_management_agent(agent) else "worker_models") or []]
    if not candidates:
        candidates = ["openai/gpt-oss-120b:free"] if is_management_agent(agent) else ["poolside/laguna-m.1:free"]

    for model in candidates:
        if not model or model == failed_model:
            continue
        if is_model_available(model):
            return model
    for model in candidates:
        if model and model != failed_model and model_capabilities(model) is not None:
            return model
    return ""


def is_management_agent(agent: AgentProfile) -> bool:
    worker_type = agent.worker_type.lower()
    role = agent.role.lower()
    return (
        "manager" in worker_type
        or agent.manager_id is None
        or DELEGATE_TASK in agent.permissions
        or any(token in role for token in ("chief", "officer", "vp", "lead", "manager", "director"))
    )


def is_model_available(model: str) -> bool:
    capabilities = model_capabilities(model)
    if capabilities is None:
        return False
    provider = str(capabilities.get("provider") or "openrouter")
    if provider == "openrouter":
        return bool(os.getenv("OPENROUTER_API_KEY"))
    if provider == "nvidia":
        return bool(os.getenv("NVIDIA_API_KEY"))
    return True


def _failover_config() -> dict[str, Any]:
    return dict(load_runtime_config().get("model_failover") or {})
