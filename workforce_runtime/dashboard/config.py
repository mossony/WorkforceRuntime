from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_DASHBOARD_CONFIG: dict[str, Any] = {
    "dashboard": {
        "refresh_interval_ms": 5000,
        "max_visible_agents": 80,
        "collapse_depth": 3,
        "show_idle_activity": True,
    },
    "activity": {
        "recent_event_limit": 300,
        "event_scan_limit": 1200,
        "recent_output_items": 12,
        "recent_tool_items": 12,
        "recent_event_items": 10,
        "full_stream_limit": 200,
        "global_output_limit": 200,
        "worker_output_limit": 80,
    },
    "summaries": {
        "mode": "local",
        "max_chars": 140,
        "active_window_seconds": 120,
        "llm": {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1/chat/completions",
            "model": "openai/gpt-oss-120b:free",
            "api_key_env": "OPENROUTER_API_KEY",
            "reasoning_enabled": True,
            "stream": False,
        },
    },
    "icons": {
        "codex": {"label": "Codex", "image_url": "/assets/agent-icons/codex.png"},
        "claude": {"label": "Claude", "image_url": ""},
        "poolside": {"label": "Pool", "image_url": ""},
        "manager": {"label": "Mgr", "image_url": ""},
        "executive": {"label": "CEO", "image_url": ""},
        "generic": {"label": "AI", "image_url": ""},
    },
}


def load_dashboard_config(path: str | Path | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_DASHBOARD_CONFIG)
    if path is None:
        return config

    config_path = Path(path)
    overrides = json.loads(config_path.read_text())
    if not isinstance(overrides, dict):
        raise ValueError(f"dashboard config must be a JSON object: {config_path}")
    return merge_dashboard_config(overrides)


def merge_dashboard_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_DASHBOARD_CONFIG)
    if overrides:
        _deep_update(config, overrides)
    return config


def _deep_update(target: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
