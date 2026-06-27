from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from workforce_runtime.config.model_registry import DEFAULT_MODEL_REGISTRY


DEFAULT_RUNTIME_CONFIG_PATH = Path("workforce_runtime_config.json")

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "runtime": {
        "store_backend": "mysql",
        "db_path": "workforce_runtime",
        "workspace_root": ".workforce_runtime",
        "task_trace_dir": "",
        "task_trace_include_file_contents": True,
        "task_trace_max_file_bytes": 500000,
    },
    "mysql": {
        "host": "127.0.0.1",
        "port": 3306,
        "username": "workforce",
        "password": "workforce",
        "database": "workforce_runtime",
        "charset": "utf8mb4",
        "connect_timeout": 10,
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
    "nvidia": {
        "chat_completions_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "api_key_env": "NVIDIA_API_KEY",
        "timeout_seconds": 120,
        "reasoning_enabled": False,
        "stream": False,
        "response_format_enabled": False,
    },
    "models": copy.deepcopy(DEFAULT_MODEL_REGISTRY["models"]),
    "org_designer": {
        "company_name": "Designed Workforce",
        "headcount_limit": 6,
        "token_budget": 600000,
        "management_model": "openai/gpt-oss-120b:free",
        "worker_model": "poolside/laguna-xs.2:free",
        "decision_backend": "codex",
        "management_worker_type": "codex",
        "worker_worker_type": "codex",
        "include_hr": True,
        "max_management_depth": 3,
        "use_llm": True,
    },
    "designed_task": {
        "company_name": "Designed Task Workforce",
        "headcount_limit": 6,
        "token_budget": 600000,
        "management_model": "openai/gpt-oss-120b:free",
        "worker_model": "poolside/laguna-m.1:free",
        "judge_model": "openai/gpt-oss-120b:free",
        "decision_backend": "codex",
        "management_worker_type": "codex",
        "worker_worker_type": "codex",
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
        "simple_level_agent_limit": 8,
        "state_agent_limit": 60,
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
    "model_failover": {
        "enabled": True,
        "management_fallback_models": [
            "openai/gpt-oss-120b:free",
            "openrouter/owl-alpha",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ],
        "worker_fallback_models": [
            "poolside/laguna-m.1:free",
            "cohere/north-mini-code:free",
            "openrouter/owl-alpha",
            "poolside/laguna-xs.2:free",
            "openai/gpt-oss-20b:free",
        ],
        "unavailable_error_fragments": [
            "model not found",
            "model metadata",
            "no endpoints",
            "invalid model",
            "unknown model",
            "does not exist",
            "degraded function cannot be invoked",
            "function cannot be invoked",
        ],
    },
    "queue": {
        "max_active_agents": 20,
        "lease_seconds": 300,
        "per_kind_limits": {
            "llm_request": 10,
            "tool_call": 20,
            "worker_run": 10,
        },
        "per_model_limits": {},
        "per_tool_limits": {},
        "allow_same_agent_parallel": False,
    },
    "agent_inbox": {
        "backend": "rabbitmq",
        "rabbitmq": {
            "host": "127.0.0.1",
            "port": 5672,
            "username": "workforce",
            "password": "workforce",
            "virtual_host": "/",
            "exchange": "workforce.agent_inbox",
            "queue_prefix": "workforce.agent.",
            "heartbeat": 30,
            "blocked_connection_timeout": 30,
        },
    },
    "skills": {
        "enabled": True,
        "materialize_on_worker_start": True,
        "roots": {
            "codex": ".agents/skills",
            "claude_code": ".claude/skills",
        },
    },
    "external_mcp": {
        "enabled": True,
        "queue_calls": True,
        "default_queue_enabled": True,
        "oauth": {
            "callback_url": "",
            "callback_port": None,
            "timeout_seconds": 300,
        },
        "servers": [
            {
                "id": "github_copilot",
                "enabled": False,
                "transport": "http",
                "url": "https://api.githubcopilot.com/mcp/",
                "tool_prefix": "github",
                "auth": {"type": "bearer", "token_env": "GITHUB_PAT_TOKEN"},
                "allowed_agent_ids": ["*"],
                "allowed_roles": [],
                "allowed_departments": [],
                "allowed_worker_types": [],
                "allowed_tools": ["*"],
                "timeout_seconds": 30,
                "queue": {"enabled": True},
                "tools": [],
            }
        ],
    },
    "execution": {
        "mode": "full_access",
        "sandbox": {
            "provider": "anthropic_sandbox_runtime",
            "command_prefix": ["srt", "--settings", "{settings_path}"],
            "settings_path": "examples/sandbox_runtime_settings.json",
            "queue_mcp_tools": True,
            "mcp_tool_queue_timeout_seconds": 30.0,
            "mcp_tool_queue_excluded_tools": [
                "enqueue_work",
                "claim_work",
                "complete_work",
                "fail_work",
                "get_work_queue",
                "get_inbox",
                "claim_inbox",
                "complete_inbox",
                "fail_inbox",
            ],
            "worker_extra_args": {
                "codex": [],
                "claude_code": ["--dangerously-skip-permissions"],
                "claude_code_interactive": [],
            },
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
        "claude_code_interactive": {
            "executable": "ccr",
            "args": ["code", "--dangerously-skip-permissions"],
            "command": None,
            "timeout_seconds": 900,
            "idle_finish_seconds": 2.0,
            "input_submit_delay_seconds": 0.35,
            "steer_interrupt_seconds": 0.8,
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
        "large_org_scale": {
            "workspace": ".workforce_runtime/demo/large-org-scale",
            "agent_count": 3000,
            "active_agent_limit": 20,
            "management_model": "openai/gpt-oss-120b:free",
            "worker_model": "poolside/laguna-m.1:free",
        },
        "large_task_100": {
            "workspace": ".workforce_runtime/demo/large-task-100",
            "plan_path": "examples/Large_Task_100_v0.md",
            "agent_count": 100,
            "active_agent_limit": 25,
            "management_models": [
                "openai/gpt-oss-120b:free",
                "openrouter/owl-alpha",
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "nvidia/nemotron-3-super-120b-a12b:free",
            ],
            "worker_models": [
                "poolside/laguna-m.1:free",
                "cohere/north-mini-code:free",
                "openrouter/owl-alpha",
                "poolside/laguna-xs.2:free",
                "openai/gpt-oss-20b:free",
            ],
            "llm_json": {
                "max_retries": 2,
                "max_tokens": 4000,
                "reasoning_enabled": False,
                "stream": True,
                "retry_initial_delay_seconds": 0.5,
                "retry_backoff_multiplier": 2.0,
                "retry_max_delay_seconds": 5.0,
            },
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
            "max_tokens": 8000,
            "reasoning_enabled": False,
            "stream": False,
            "retry_initial_delay_seconds": 0.25,
            "retry_backoff_multiplier": 2.0,
            "retry_max_delay_seconds": 2.0,
        },
        "swe_bench": {
            "dataset": "SWE-bench/SWE-bench_Lite",
            "model": "poolside/laguna-m.1:free",
            "max_tokens": 12000,
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
        "queue": copy.deepcopy(config.get("queue", {})),
        "skills": copy.deepcopy(config.get("skills", {})),
        "execution": copy.deepcopy(config.get("execution", {})),
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
