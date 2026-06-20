from __future__ import annotations

import json

import pytest

import workforce_runtime.llm.openrouter as openrouter
from workforce_runtime.llm import OpenRouterClient, extract_json_object


def test_extract_json_object_handles_inline_fenced_json() -> None:
    payload = extract_json_object('```json{ "title": "Delegate", "ok": true }')

    assert payload == {"title": "Delegate", "ok": True}


def test_extract_json_object_uses_first_balanced_object() -> None:
    payload = extract_json_object('prefix {"answer": "hi"} trailing note }')

    assert payload == {"answer": "hi"}


def test_stream_raises_clear_error_when_only_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self):
            payload = {
                "choices": [
                    {
                        "delta": {"reasoning": "thinking", "content": ""},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"total_tokens": 5},
            }
            return iter([f"data: {json.dumps(payload)}\n".encode(), b"data: [DONE]\n"])

    monkeypatch.setattr(openrouter, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    client = OpenRouterClient(api_key="test", timeout_seconds=5)

    with pytest.raises(RuntimeError, match="no assistant content"):
        client.chat(
            model="openai/gpt-oss-120b:free",
            messages=[{"role": "user", "content": "Return JSON."}],
            stream=True,
        )
