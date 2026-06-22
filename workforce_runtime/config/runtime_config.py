from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from workforce_runtime.config.model_registry import DEFAULT_MODEL_REGISTRY


DEFAULT_RUNTIME_CONFIG_PATH = Path("workforce_runtime_config.json")

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "runtime": {
        "db_path": ".workforce_runtime/runtime.sqlite",
        "workspace_root": ".workforce_runtime",
        "task_trace_dir": "",
        "task_trace_include_file_contents": True,
        "task_trace_max_file_bytes": 500000,
    },
    "openrouter": {
        "chat_completions_url": "https://openrouter.ai/api/v1/chat/completions",
        "api_key_env": "OPENROUTER_API_KEY",
        "timeout_seconds": 90,
        "reasoning_enabled": True,
        "stream": False,
        "http_referer": "https://github.com/openai/workforce-runtime",
        "x_title": "Workforce Runtime",
    },
    "models": copy.deepcopy(DEFAULT_MODEL_REGISTRY["models"]),
    "org_designer": {
        "company_name": "Designed Workforce",
        "headcount_limit": 6,
        "token_budget": 600000,
        "management_model": "openai/gpt-oss-120b:free",
        "worker_model": "poolside/laguna-xs.2:free",
        "include_hr": True,
        "max_management_depth": 3,
        "use_llm": False,
    },
    "designed_task": {
        "company_name": "Designed Task Workforce",
        "headcount_limit": 6,
        "token_budget": 600000,
        "management_model": "openai/gpt-oss-120b:free",
        "worker_model": "poolside/laguna-m.1:free",
        "judge_model": "openai/gpt-oss-120b:free",
        "use_llm": True,
        "judge": "heuristic",
        "reset": False,
        "constraints": ["Preserve the user's stated objective."],
        "acceptance_criteria": [
            "Produce a concise result artifact.",
            "Report evidence, risks, and next action.",
        ],
        "expected_artifacts": ["task_result"],
    },
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8765,
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
    "workers": {
        "codex": {
            "executable": "codex",
            "profile": "workforce-openrouter",
            "model": None,
            "approval_policy": "never",
            "sandbox_mode": "workspace-write",
            "timeout_seconds": None,
        },
        "claude_code": {
            "executable": "claude",
            "timeout_seconds": None,
        },
        "generic_cli": {
            "default_timeout_seconds": None,
        },
    },
    "demos": {
        "sample_repo_fix_workspace": ".workforce_runtime/demo/sample-repo-fix",
        "long_rfc": {
            "url": "https://www.rfc-editor.org/rfc/rfc9110.txt",
            "delay_seconds": 0.8,
            "worker_timeout_seconds": 90,
        },
        "web_research": {
            "worker_timeout_seconds": 60,
        },
        "simple_status": {
            "worker_timeout_seconds": 30,
        },
    },
    "benchmarks": {
        "default_case_path": "examples/benchmarks/web_research_real_llm.json",
        "workspace": ".workforce_runtime/benchmark/workspace",
        "use_llm": False,
        "judge": "heuristic",
        "reset": True,
        "source_excerpt_chars": 20000,
        "llm_json": {
            "max_retries": 2,
            "max_tokens": 4000,
            "reasoning_enabled": False,
            "stream": False,
            "retry_initial_delay_seconds": 0.25,
            "retry_backoff_multiplier": 2.0,
            "retry_max_delay_seconds": 2.0,
        },
        "swe_bench": {
            "dataset": "SWE-bench/SWE-bench_Lite",
            "model": "poolside/laguna-m.1:free",
            "max_tokens": 6000,
            "test_timeout_seconds": 600,
            "setup_timeout_seconds": 900,
        },
    },
}


def load_runtime_config(path: str | Path | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_RUNTIME_CONFIG)
    config_path = Path(path) if path is not None else DEFAULT_RUNTIME_CONFIG_PATH
    if not config_path.exists():
        return config
    overrides = json.loads(config_path.read_text())
    if not isinstance(overrides, dict):
        raise ValueError(f"runtime config must be a JSON object: {config_path}")
    _deep_update(config, overrides)
    return config


def save_runtime_config(config: dict[str, Any], path: str | Path | None = None) -> Path:
    if not isinstance(config, dict):
        raise ValueError("runtime config must be a JSON object")
    config_path = Path(path) if path is not None else DEFAULT_RUNTIME_CONFIG_PATH
    merged = merge_runtime_config(config)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(merged, indent=2) + "\n")
    return config_path


def merge_runtime_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_RUNTIME_CONFIG)
    if overrides:
        _deep_update(config, overrides)
    return config


def dashboard_config_from_runtime(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "dashboard": copy.deepcopy(config.get("dashboard", {})),
        "activity": copy.deepcopy(config.get("activity", {})),
        "summaries": copy.deepcopy(config.get("summaries", {})),
        "icons": copy.deepcopy(config.get("icons", {})),
        "models": copy.deepcopy(config.get("models", {})),
    }


def runtime_config_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_RUNTIME_CONFIG_PATH


def _deep_update(target: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
