from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workforce_runtime.config.model_registry import model_capabilities
from workforce_runtime.config.runtime_config import load_runtime_config

try:
    from cerebras.cloud.sdk import Cerebras as CerebrasSDK
except ImportError:  # pragma: no cover - only hit when dependencies are missing
    CerebrasSDK = None

try:
    from groq import Groq as GroqSDK
except ImportError:  # pragma: no cover - only hit when dependencies are missing
    GroqSDK = None


OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
NVIDIA_CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
CEREBRAS_BASE_URL = "https://api.cerebras.ai"
GROQ_BASE_URL = "https://api.groq.com"


@dataclass(frozen=True)
class OpenAICompatibleResponse:
    content: str
    raw: dict[str, Any]
    usage: dict[str, Any]
    reasoning_details: Any = None


OpenRouterResponse = OpenAICompatibleResponse


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        api_key_env: str | None = None,
        provider_config_key: str = "openrouter",
        provider_name: str | None = None,
        default_extra_body: dict[str, Any] | None = None,
    ) -> None:
        config = load_runtime_config().get(provider_config_key, {})
        env_key = api_key_env or str(config.get("api_key_env") or "OPENROUTER_API_KEY")
        self.api_key = api_key or os.environ.get(env_key, "")
        default_base_url = NVIDIA_CHAT_COMPLETIONS_URL if provider_config_key == "nvidia" else OPENROUTER_CHAT_COMPLETIONS_URL
        self.base_url = base_url or str(config.get("chat_completions_url") or default_base_url)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else int(config.get("timeout_seconds") or 90)
        self.api_key_env = env_key
        self.provider_name = provider_name or provider_config_key
        self.default_reasoning = bool(config.get("reasoning_enabled", True))
        self.response_format_enabled = bool(config.get("response_format_enabled", True))
        self.http_referer = str(config.get("http_referer") or "")
        self.x_title = str(config.get("x_title") or "")
        self.default_extra_body = dict(config.get("extra_body") or {})
        if default_extra_body:
            self.default_extra_body.update(default_extra_body)

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning: bool | None = None,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        on_delta: Callable[[str], None] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> OpenAICompatibleResponse:
        if not self.is_configured():
            raise RuntimeError(f"{self.api_key_env} is not configured")

        capabilities = model_capabilities(model) or {}
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        effective_reasoning = self.default_reasoning if reasoning is None else reasoning
        if bool(capabilities.get("requires_reasoning")):
            effective_reasoning = True
        if effective_reasoning:
            body["reasoning"] = {"enabled": True}
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if response_format is not None and (
            self.response_format_enabled or bool(capabilities.get("supports_response_format"))
        ):
            body["response_format"] = response_format
        model_extra_body = dict(capabilities.get("default_extra_body") or {})
        if self.default_extra_body:
            body.update(self.default_extra_body)
        if model_extra_body:
            body.update(model_extra_body)
        if extra_body:
            body.update(extra_body)

        if stream:
            return self._chat_stream(body, on_delta=on_delta)
        return self._chat_once(body)

    def _request(self, body: dict[str, Any]) -> Request:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.x_title:
            headers["X-Title"] = self.x_title
        return Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def _chat_once(self, body: dict[str, Any]) -> OpenAICompatibleResponse:
        try:
            with urlopen(self._request(body), timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.provider_name} request failed: HTTP {exc.code}: {detail}") from exc

        message = raw.get("choices", [{}])[0].get("message") or {}
        return OpenAICompatibleResponse(
            content=str(message.get("content") or ""),
            raw=raw,
            usage=dict(raw.get("usage") or {}),
            reasoning_details=message.get("reasoning_details"),
        )

    def _chat_stream(
        self,
        body: dict[str, Any],
        *,
        on_delta: Callable[[str], None] | None,
    ) -> OpenAICompatibleResponse:
        chunks: list[str] = []
        last_payload: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        reasoning_chars = 0
        try:
            with urlopen(self._request(body), timeout=self.timeout_seconds) as response:
                deadline = time.monotonic() + self.timeout_seconds
                for raw_line in response:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"{self.provider_name} stream exceeded {self.timeout_seconds} seconds")
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    last_payload = payload
                    if payload.get("error"):
                        raise RuntimeError(f"{self.provider_name} stream returned error: {_format_provider_error(payload['error'])}")
                    if isinstance(payload.get("usage"), dict):
                        usage = dict(payload["usage"])
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning_chars += len(str(delta.get("reasoning") or ""))
                    text = delta.get("content") or ""
                    if text:
                        chunks.append(str(text))
                        if on_delta is not None:
                            on_delta(str(text))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.provider_name} stream failed: HTTP {exc.code}: {detail}") from exc

        if not chunks:
            finish_reason = ""
            choices = last_payload.get("choices") or []
            if choices:
                finish_reason = str(choices[0].get("finish_reason") or choices[0].get("native_finish_reason") or "")
            raise RuntimeError(
                f"{self.provider_name} stream returned no assistant content"
                f" (finish_reason={finish_reason or 'unknown'}, reasoning_chars={reasoning_chars})."
                " Increase max_tokens, disable/reduce reasoning for structured JSON tasks, or retry without streaming."
            )

        return OpenAICompatibleResponse(
            content="".join(chunks),
            raw=last_payload,
            usage=usage or dict(last_payload.get("usage") or {}),
        )


class OpenRouterClient(OpenAICompatibleClient):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("provider_config_key", "openrouter")
        kwargs.setdefault("provider_name", "OpenRouter")
        super().__init__(**kwargs)


class NvidiaClient(OpenAICompatibleClient):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("provider_config_key", "nvidia")
        kwargs.setdefault("provider_name", "NVIDIA")
        super().__init__(**kwargs)


class CerebrasClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        api_key_env: str | None = None,
        provider_config_key: str = "cerebras",
        provider_name: str = "Cerebras",
        sdk_client: Any | None = None,
    ) -> None:
        config = load_runtime_config().get(provider_config_key, {})
        env_key = api_key_env or str(config.get("api_key_env") or "CEREBRAS_API_KEY")
        self.api_key = api_key or os.environ.get(env_key, "")
        self.base_url = base_url or str(config.get("base_url") or CEREBRAS_BASE_URL)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else int(config.get("timeout_seconds") or 120)
        self.api_key_env = env_key
        self.provider_name = provider_name
        self.default_reasoning = bool(config.get("reasoning_enabled", True))
        self.reasoning_effort = str(config.get("reasoning_effort") or "medium")
        self.response_format_enabled = bool(config.get("response_format_enabled", True))
        self.default_extra_body = dict(config.get("extra_body") or {})
        self._sdk_client = sdk_client

    def is_configured(self) -> bool:
        return bool(self.api_key.strip()) or self._sdk_client is not None

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning: bool | None = None,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        on_delta: Callable[[str], None] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> OpenAICompatibleResponse:
        if not self.is_configured():
            raise RuntimeError(f"{self.api_key_env} is not configured")
        if self._sdk_client is None:
            if CerebrasSDK is None:
                raise RuntimeError("cerebras-cloud-sdk is not installed")
            self._sdk_client = CerebrasSDK(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)

        capabilities = model_capabilities(model) or {}
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        effective_reasoning = self.default_reasoning if reasoning is None else reasoning
        if bool(capabilities.get("requires_reasoning")):
            effective_reasoning = True
        if effective_reasoning:
            body["reasoning_effort"] = str(capabilities.get("reasoning_effort") or self.reasoning_effort)
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if response_format is not None and (
            self.response_format_enabled or bool(capabilities.get("supports_response_format"))
        ):
            body["response_format"] = response_format
        model_extra_body = dict(capabilities.get("default_extra_body") or {})
        if self.default_extra_body:
            body.update(self.default_extra_body)
        if model_extra_body:
            body.update(model_extra_body)
        if extra_body:
            body.update(extra_body)

        if stream:
            return self._chat_stream(body, on_delta=on_delta)

        response = self._sdk_client.chat.completions.create(**body)
        message = _first_choice_message(response)
        return OpenAICompatibleResponse(
            content=str(_object_get(message, "content") or ""),
            raw=_object_to_dict(response),
            usage=_object_to_dict(_object_get(response, "usage")),
            reasoning_details=_object_get(message, "reasoning"),
        )

    def _chat_stream(
        self,
        body: dict[str, Any],
        *,
        on_delta: Callable[[str], None] | None,
    ) -> OpenAICompatibleResponse:
        chunks: list[str] = []
        last_payload: Any = None
        usage: dict[str, Any] = {}
        for event in self._sdk_client.chat.completions.create(**body):
            last_payload = event
            if _object_get(event, "usage") is not None:
                usage = _object_to_dict(_object_get(event, "usage"))
            choices = _object_get(event, "choices") or []
            if not choices:
                continue
            delta = _object_get(choices[0], "delta") or {}
            text = _object_get(delta, "content") or ""
            if text:
                chunks.append(str(text))
                if on_delta is not None:
                    on_delta(str(text))

        if not chunks:
            raise RuntimeError(f"{self.provider_name} stream returned no assistant content")

        return OpenAICompatibleResponse(
            content="".join(chunks),
            raw=_object_to_dict(last_payload),
            usage=usage or _object_to_dict(_object_get(last_payload, "usage")),
        )


class GroqClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        api_key_env: str | None = None,
        provider_config_key: str = "groq",
        provider_name: str = "Groq",
        sdk_client: Any | None = None,
    ) -> None:
        config = load_runtime_config().get(provider_config_key, {})
        env_key = api_key_env or str(config.get("api_key_env") or "GROQ_API_KEY")
        self.api_key = api_key or os.environ.get(env_key, "")
        self.base_url = base_url or str(config.get("base_url") or GROQ_BASE_URL)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else int(config.get("timeout_seconds") or 120)
        self.api_key_env = env_key
        self.provider_name = provider_name
        self.default_reasoning = bool(config.get("reasoning_enabled", True))
        self.reasoning_effort = str(config.get("reasoning_effort") or "medium")
        self.response_format_enabled = bool(config.get("response_format_enabled", True))
        self.default_extra_body = dict(config.get("extra_body") or {})
        self._sdk_client = sdk_client

    def is_configured(self) -> bool:
        return bool(self.api_key.strip()) or self._sdk_client is not None

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning: bool | None = None,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        on_delta: Callable[[str], None] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> OpenAICompatibleResponse:
        if not self.is_configured():
            raise RuntimeError(f"{self.api_key_env} is not configured")
        if self._sdk_client is None:
            if GroqSDK is None:
                raise RuntimeError("groq is not installed")
            self._sdk_client = GroqSDK(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)

        capabilities = model_capabilities(model) or {}
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        effective_reasoning = self.default_reasoning if reasoning is None else reasoning
        if bool(capabilities.get("requires_reasoning")):
            effective_reasoning = True
        if effective_reasoning:
            body["reasoning_effort"] = str(capabilities.get("reasoning_effort") or self.reasoning_effort)
        if stream:
            body["stream"] = True
        if response_format is not None and (
            self.response_format_enabled or bool(capabilities.get("supports_response_format"))
        ):
            body["response_format"] = response_format
        model_extra_body = dict(capabilities.get("default_extra_body") or {})
        if self.default_extra_body:
            body.update(self.default_extra_body)
        if model_extra_body:
            body.update(model_extra_body)
        if extra_body:
            body.update(extra_body)

        if stream:
            return self._chat_stream(body, on_delta=on_delta)

        response = self._sdk_client.chat.completions.create(**body)
        message = _first_choice_message(response)
        return OpenAICompatibleResponse(
            content=str(_object_get(message, "content") or ""),
            raw=_object_to_dict(response),
            usage=_object_to_dict(_object_get(response, "usage")),
            reasoning_details=_object_get(message, "reasoning"),
        )

    def _chat_stream(
        self,
        body: dict[str, Any],
        *,
        on_delta: Callable[[str], None] | None,
    ) -> OpenAICompatibleResponse:
        chunks: list[str] = []
        last_payload: Any = None
        usage: dict[str, Any] = {}
        for event in self._sdk_client.chat.completions.create(**body):
            last_payload = event
            if _object_get(event, "usage") is not None:
                usage = _object_to_dict(_object_get(event, "usage"))
            choices = _object_get(event, "choices") or []
            if not choices:
                continue
            delta = _object_get(choices[0], "delta") or {}
            text = _object_get(delta, "content") or ""
            if text:
                chunks.append(str(text))
                if on_delta is not None:
                    on_delta(str(text))

        if not chunks:
            raise RuntimeError(f"{self.provider_name} stream returned no assistant content")

        return OpenAICompatibleResponse(
            content="".join(chunks),
            raw=_object_to_dict(last_payload),
            usage=usage or _object_to_dict(_object_get(last_payload, "usage")),
        )


class RoutedLLMClient:
    def __init__(self, *, clients: dict[str, OpenAICompatibleClient] | None = None) -> None:
        self.clients = clients or {
            "openrouter": OpenRouterClient(),
            "nvidia": NvidiaClient(),
            "cerebras": CerebrasClient(),
            "groq": GroqClient(),
        }

    def is_configured(self) -> bool:
        return any(client.is_configured() for client in self.clients.values())

    def chat(self, *, model: str, **kwargs: Any) -> OpenAICompatibleResponse:
        provider = str((model_capabilities(model) or {}).get("provider") or "openrouter")
        client = self.clients.get(provider)
        if client is None:
            raise RuntimeError(f"no LLM client configured for provider: {provider}")
        return client.chat(model=model, **kwargs)


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates: list[str] = []
    for candidate in (stripped, _strip_code_fence(stripped)):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: Exception | None = None
    for candidate in candidates:
        for value_text in (candidate, _first_balanced_json_object(candidate)):
            if not value_text:
                continue
            try:
                value = json.loads(value_text)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if not isinstance(value, dict):
                raise ValueError("expected a JSON object")
            return value
    raise ValueError("expected a JSON object in model response") from last_error


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        first = lines[0][3:].strip()
        if first.lower().startswith("json"):
            first = first[4:].strip()
        if first:
            lines[0] = first
        else:
            lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        last = lines[-1][3:].strip()
        if last:
            lines[-1] = last
        else:
            lines = lines[:-1]
    return "\n".join(lines).strip()


def _first_balanced_json_object(text: str) -> str:
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _object_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _object_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _first_choice_message(response: Any) -> Any:
    choices = _object_get(response, "choices") or []
    if not choices:
        return {}
    return _object_get(choices[0], "message") or {}


def _format_provider_error(error: Any) -> str:
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error") or error)
        code = error.get("code")
        return f"{code}: {message}" if code else message
    return str(error)
