from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from workforce_runtime.config.runtime_config import DEFAULT_RUNTIME_CONFIG, dashboard_config_from_runtime

DEFAULT_DASHBOARD_CONFIG: dict[str, Any] = dashboard_config_from_runtime(DEFAULT_RUNTIME_CONFIG)


def load_dashboard_config(path: str | Path | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_DASHBOARD_CONFIG)
    if path is None:
        return config

    config_path = Path(path)
    overrides = json.loads(config_path.read_text())
    if not isinstance(overrides, dict):
        raise ValueError(f"dashboard config must be a JSON object: {config_path}")
    if any(key in overrides for key in ("runtime", "openrouter", "org_designer", "workers", "benchmarks")):
        return merge_dashboard_config(dashboard_config_from_runtime(overrides))
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
