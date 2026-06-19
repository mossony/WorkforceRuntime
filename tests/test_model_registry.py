from __future__ import annotations

import json
from pathlib import Path

from workforce_runtime.config import format_model_context_note, load_model_registry, model_capabilities
from workforce_runtime.core import AgentProfile, Company, generate_system_prompt


def test_default_model_registry_knows_openrouter_context_windows() -> None:
    assert model_capabilities("openai/gpt-oss-120b:free")["context_window_tokens"] == 131072
    assert model_capabilities("poolside/laguna-m.1:free")["context_window_tokens"] == 262144
    assert model_capabilities("poolside/laguna-xs.2:free")["max_output_tokens"] == 32768
    assert "context window 131,072 tokens" in format_model_context_note("openai/gpt-oss-120b:free")


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
