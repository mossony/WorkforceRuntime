from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workforce_runtime.config.runtime_config import load_runtime_config


OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class OpenRouterResponse:
    content: str
    raw: dict[str, Any]
    usage: dict[str, Any]
    reasoning_details: Any = None


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        api_key_env: str | None = None,
    ) -> None:
        config = load_runtime_config().get("openrouter", {})
        env_key = api_key_env or str(config.get("api_key_env") or "OPENROUTER_API_KEY")
        self.api_key = api_key or os.environ.get(env_key, "")
        self.base_url = base_url or str(config.get("chat_completions_url") or OPENROUTER_CHAT_COMPLETIONS_URL)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else int(config.get("timeout_seconds") or 90)
        self.http_referer = str(config.get("http_referer") or "https://github.com/openai/workforce-runtime")
        self.x_title = str(config.get("x_title") or "Workforce Runtime")

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning: bool = True,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        on_delta: Callable[[str], None] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> OpenRouterResponse:
        if not self.is_configured():
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if reasoning:
            body["reasoning"] = {"enabled": True}
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if response_format is not None:
            body["response_format"] = response_format
        if extra_body:
            body.update(extra_body)

        if stream:
            return self._chat_stream(body, on_delta=on_delta)
        return self._chat_once(body)

    def _request(self, body: dict[str, Any]) -> Request:
        return Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.http_referer,
                "X-Title": self.x_title,
            },
            method="POST",
        )

    def _chat_once(self, body: dict[str, Any]) -> OpenRouterResponse:
        try:
            with urlopen(self._request(body), timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenRouter request failed: HTTP {exc.code}: {detail}") from exc

        message = raw.get("choices", [{}])[0].get("message") or {}
        return OpenRouterResponse(
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
    ) -> OpenRouterResponse:
        chunks: list[str] = []
        last_payload: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        reasoning_chars = 0
        try:
            with urlopen(self._request(body), timeout=self.timeout_seconds) as response:
                deadline = time.monotonic() + self.timeout_seconds
                for raw_line in response:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"OpenRouter stream exceeded {self.timeout_seconds} seconds")
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
                        raise RuntimeError(f"OpenRouter stream returned error: {_format_openrouter_error(payload['error'])}")
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
            raise RuntimeError(f"OpenRouter stream failed: HTTP {exc.code}: {detail}") from exc

        if not chunks:
            finish_reason = ""
            choices = last_payload.get("choices") or []
            if choices:
                finish_reason = str(choices[0].get("finish_reason") or choices[0].get("native_finish_reason") or "")
            raise RuntimeError(
                "OpenRouter stream returned no assistant content"
                f" (finish_reason={finish_reason or 'unknown'}, reasoning_chars={reasoning_chars})."
                " Increase max_tokens, disable/reduce reasoning for structured JSON tasks, or retry without streaming."
            )

        return OpenRouterResponse(
            content="".join(chunks),
            raw=last_payload,
            usage=usage or dict(last_payload.get("usage") or {}),
        )


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


def _format_openrouter_error(error: Any) -> str:
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error") or error)
        code = error.get("code")
        return f"{code}: {message}" if code else message
    return str(error)
