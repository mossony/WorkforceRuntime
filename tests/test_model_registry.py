from __future__ import annotations

import json
from pathlib import Path

from workforce_runtime.config import (
    choose_agent_replacement_model,
    format_model_context_note,
    is_unavailable_model_error,
    load_model_registry,
    model_capabilities,
)
from workforce_runtime.core import AgentProfile, Company, generate_system_prompt


def test_default_model_registry_knows_openrouter_context_windows() -> None:
    assert model_capabilities("gpt-oss-120b")["provider"] == "cerebras"
    assert model_capabilities("gpt-oss-120b")["reasoning_effort"] == "medium"
    assert model_capabilities("openai/gpt-oss-120b")["provider"] == "groq"
    assert model_capabilities("openai/gpt-oss-120b")["reasoning_effort"] == "medium"
    assert model_capabilities("openai/gpt-oss-120b:free")["context_window_tokens"] == 131072
    assert model_capabilities("poolside/laguna-m.1:free")["context_window_tokens"] == 262144
    assert model_capabilities("poolside/laguna-xs.2:free")["max_output_tokens"] == 32768
    assert model_capabilities("openrouter/owl-alpha")["context_window_tokens"] == 1048756
    assert model_capabilities("nvidia/nemotron-3-ultra-550b-a55b:free")["provider"] == "openrouter"
    assert model_capabilities("nvidia/nemotron-3-super-120b-a12b:free")["context_window_tokens"] == 1000000
    assert model_capabilities("cohere/north-mini-code:free")["max_output_tokens"] == 64000
    assert model_capabilities("openai/gpt-oss-20b:free")["requires_reasoning"] is True
    assert model_capabilities("deepseek-ai/deepseek-v4-pro") is None
    assert model_capabilities("z-ai/glm-5.1") is None
    assert model_capabilities("moonshotai/kimi-k2.6") is None
    assert "context window 131,072 tokens" in format_model_context_note("openai/gpt-oss-120b:free")
    assert "context window 1,048,756 tokens" in format_model_context_note("openrouter/owl-alpha")


def test_model_registry_json_overrides_defaults(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "models": {
                    "custom/model": {
                        "provider": "openrouter",
                        "context_window_tokens": 4096,
                        "max_output_tokens": 1024,
                    }
                }
            }
        )
    )

    registry = load_model_registry(path)

    assert registry["models"]["custom/model"]["context_window_tokens"] == 4096
    assert registry["models"]["openai/gpt-oss-120b:free"]["context_window_tokens"] == 131072


def test_generated_system_prompt_includes_known_model_limits() -> None:
    company = Company(name="Demo")
    agent = AgentProfile(
        id="worker",
        name="Worker",
        role="Software Engineer",
        department="Engineering",
        worker_type="generic_cli",
        model="poolside/laguna-m.1:free",
    )

    prompt = generate_system_prompt(company, agent)

    assert "Assigned model: poolside/laguna-m.1:free." in prompt
    assert "Model limits: context window 262,144 tokens, max output 32,768 tokens." in prompt


def test_model_failover_detects_unavailable_model_errors_and_selects_role_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    manager = AgentProfile(
        id="manager",
        name="Manager",
        role="Engineering Manager",
        department="Engineering",
        worker_type="openrouter_manager",
        model="deepseek-ai/deepseek-v4-pro",
    )
    worker = AgentProfile(
        id="worker",
        name="Worker",
        role="Software Engineer",
        department="Engineering",
        manager_id="manager",
        worker_type="openrouter_worker",
        model="moonshotai/kimi-k2.6",
    )

    assert is_unavailable_model_error("NVIDIA stream failed: DEGRADED function cannot be invoked")
    assert is_unavailable_model_error("model not found")
    assert choose_agent_replacement_model(manager, failed_model=manager.model) == "gpt-oss-120b"
    assert choose_agent_replacement_model(worker, failed_model=worker.model) == "gpt-oss-120b"
