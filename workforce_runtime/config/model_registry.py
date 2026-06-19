from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_MODEL_REGISTRY: dict[str, Any] = {
    "models": {
        "openai/gpt-oss-120b:free": {
            "provider": "openrouter",
            "context_window_tokens": 131072,
            "max_output_tokens": 131072,
            "supports_reasoning": True,
            "supports_tools": True,
            "source": "https://openrouter.ai/openai/gpt-oss-120b:free",
        },
        "poolside/laguna-m.1:free": {
            "provider": "openrouter",
            "context_window_tokens": 262144,
            "max_output_tokens": 32768,
            "supports_reasoning": True,
            "supports_tools": True,
            "source": "https://openrouter.ai/poolside/laguna-m.1:free",
        },
        "poolside/laguna-xs.2:free": {
            "provider": "openrouter",
            "context_window_tokens": 262144,
            "max_output_tokens": 32768,
            "supports_reasoning": True,
            "supports_tools": True,
            "source": "https://openrouter.ai/poolside/laguna-xs.2:free",
        },
    }
}


def load_model_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry = copy.deepcopy(DEFAULT_MODEL_REGISTRY)
    if path is None:
        return registry

    config_path = Path(path)
    overrides = json.loads(config_path.read_text())
    if not isinstance(overrides, dict):
        raise ValueError(f"model registry must be a JSON object: {config_path}")
    _deep_update(registry, overrides)
    return registry


def model_capabilities(model: str, registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not model:
        return None
    data = registry or DEFAULT_MODEL_REGISTRY
    capabilities = data.get("models", {}).get(model)
    return copy.deepcopy(capabilities) if isinstance(capabilities, dict) else None


def format_model_context_note(model: str, registry: dict[str, Any] | None = None) -> str:
    capabilities = model_capabilities(model, registry)
    if capabilities is None:
        return "Model context window: unknown; keep prompts compact and rely on artifacts/context refs for large inputs."

    context = int(capabilities.get("context_window_tokens") or 0)
    output = int(capabilities.get("max_output_tokens") or 0)
    parts = []
    if context:
        parts.append(f"context window {context:,} tokens")
    if output:
        parts.append(f"max output {output:,} tokens")
    if not parts:
        return "Model context window: unknown; keep prompts compact and rely on artifacts/context refs for large inputs."
    return "Model limits: " + ", ".join(parts) + "."


def _deep_update(target: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
