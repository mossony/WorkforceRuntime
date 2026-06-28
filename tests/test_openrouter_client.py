from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import workforce_runtime.llm.openrouter as openrouter
from workforce_runtime.llm import CerebrasClient, GroqClient, NvidiaClient, OpenRouterClient, RoutedLLMClient, extract_json_object


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


def test_nvidia_client_uses_provider_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(openrouter, "urlopen", fake_urlopen)

    client = NvidiaClient(
        api_key="test",
        timeout_seconds=5,
        default_extra_body={"chat_template_kwargs": {"thinking": False}},
    )
    response = client.chat(
        model="nvidia/test-model",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=16384,
        response_format={"type": "json_object"},
    )

    assert response.content == "ok"
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["timeout"] == 5
    assert captured["body"] == {
        "model": "nvidia/test-model",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.2,
        "max_tokens": 16384,
        "chat_template_kwargs": {"thinking": False},
    }
    assert "response_format" not in captured["body"]
    assert captured["headers"]["Authorization"] == "Bearer test"
    assert "HTTP-Referer" not in captured["headers"]
    assert "X-Title" not in captured["headers"]


def test_cerebras_client_uses_sdk_reasoning_effort() -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", reasoning="hidden"))],
                usage=SimpleNamespace(total_tokens=3),
            )

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client = CerebrasClient(api_key="test", sdk_client=sdk_client, timeout_seconds=5)
    response = client.chat(
        model="gpt-oss-120b",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=64,
        reasoning=False,
        response_format={"type": "json_object"},
    )

    assert response.content == "ok"
    assert captured["model"] == "gpt-oss-120b"
    assert captured["max_tokens"] == 64
    assert captured["reasoning_effort"] == "medium"
    assert "reasoning" not in captured
    assert captured["response_format"] == {"type": "json_object"}


def test_groq_client_uses_sdk_reasoning_effort() -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", reasoning="hidden"))],
                usage=SimpleNamespace(total_tokens=3),
            )

    sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client = GroqClient(api_key="test", sdk_client=sdk_client, timeout_seconds=5)
    response = client.chat(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=64,
        reasoning=False,
        response_format={"type": "json_object"},
    )

    assert response.content == "ok"
    assert captured["model"] == "openai/gpt-oss-120b"
    assert captured["max_tokens"] == 64
    assert captured["reasoning_effort"] == "medium"
    assert "reasoning" not in captured
    assert captured["response_format"] == {"type": "json_object"}


def test_routed_client_selects_cerebras_provider() -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def is_configured(self) -> bool:
            return True

        def chat(self, *, model: str, **kwargs: object) -> object:
            captured["model"] = model
            captured["kwargs"] = kwargs
            return "ok"

    client = RoutedLLMClient(clients={"cerebras": FakeClient()})

    assert client.chat(model="gpt-oss-120b", messages=[]) == "ok"
    assert captured["model"] == "gpt-oss-120b"


def test_routed_client_selects_groq_provider() -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def is_configured(self) -> bool:
            return True

        def chat(self, *, model: str, **kwargs: object) -> object:
            captured["model"] = model
            captured["kwargs"] = kwargs
            return "ok"

    client = RoutedLLMClient(clients={"groq": FakeClient()})

    assert client.chat(model="openai/gpt-oss-120b", messages=[]) == "ok"
    assert captured["model"] == "openai/gpt-oss-120b"


def test_openrouter_client_enables_required_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(openrouter, "urlopen", fake_urlopen)

    client = OpenRouterClient(api_key="test", timeout_seconds=5)
    client.chat(
        model="openai/gpt-oss-120b:free",
        messages=[{"role": "user", "content": "hello"}],
        reasoning=False,
    )

    assert captured["body"]["reasoning"] == {"enabled": True}
