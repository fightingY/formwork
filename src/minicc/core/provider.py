from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import httpx


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    cache_hit_rate: float | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    raw: dict[str, Any]
    usage: ModelUsage
    latency_ms: int


@dataclass(frozen=True)
class CompletionOptions:
    temperature: float = 0.0
    stream: bool = False
    include_usage: bool = True


class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        ...


class ProviderError(RuntimeError):
    """Expected failure while communicating with a model provider."""


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible chat completions APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        options = options or CompletionOptions()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": options.temperature,
            "stream": options.stream,
        }
        if options.stream and options.include_usage:
            payload["stream_options"] = {"include_usage": True}

        started = time.perf_counter()
        if options.stream:
            raw, text, usage_raw = self._complete_stream(payload)
        else:
            raw = self._post_json(payload)
            text = extract_chat_text(raw)
            usage_raw = raw.get("usage") if isinstance(raw, dict) else None

        latency_ms = int((time.perf_counter() - started) * 1000)
        return ModelResponse(
            text=text,
            raw=raw,
            usage=parse_model_usage(usage_raw),
            latency_ms=latency_ms,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Provider HTTP request failed: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise ProviderError("Provider response was not a JSON object.")
        return data

    def _complete_stream(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str, Mapping[str, Any] | None]:
        chunks: list[dict[str, Any]] = []
        content_parts: list[str] = []
        usage_raw: Mapping[str, Any] | None = None

        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if not isinstance(chunk, dict):
                            continue
                        chunks.append(chunk)
                        if chunk.get("usage"):
                            usage_raw = chunk["usage"]
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if piece:
                                content_parts.append(str(piece))
        except httpx.HTTPError as exc:
            raise ProviderError(f"Provider HTTP request failed: {type(exc).__name__}") from exc

        return {"chunks": chunks, "usage": usage_raw}, "".join(content_parts), usage_raw


def extract_chat_text(raw: Mapping[str, Any]) -> str:
    choices = raw.get("choices") or []
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message") or {}
    if isinstance(message, Mapping):
        content = message.get("content")
        if content is None:
            return ""
        return str(content)
    return ""


def parse_model_usage(raw_usage: Mapping[str, Any] | None) -> ModelUsage:
    if not raw_usage:
        return ModelUsage()

    prompt_tokens = _int_or_none(raw_usage.get("prompt_tokens"))
    completion_tokens = _int_or_none(raw_usage.get("completion_tokens"))
    total_tokens = _int_or_none(raw_usage.get("total_tokens"))

    prompt_details = _mapping_or_empty(raw_usage.get("prompt_tokens_details"))
    input_details = _mapping_or_empty(raw_usage.get("input_token_details"))

    cache_hit_tokens = _first_int(
        raw_usage,
        "prompt_cache_hit_tokens",
        "cache_read_input_tokens",
        "cache_hit_tokens",
    )
    cache_miss_tokens = _first_int(
        raw_usage,
        "prompt_cache_miss_tokens",
        "cache_creation_input_tokens",
        "cache_miss_tokens",
    )
    cached_tokens = _first_present_int(
        raw_usage.get("cached_tokens"),
        prompt_details.get("cached_tokens"),
        input_details.get("cached_tokens"),
        cache_hit_tokens,
    )

    cache_hit_rate: float | None = None
    if cache_hit_tokens is not None and cache_miss_tokens is not None:
        cache_hit_rate = cache_hit_tokens / max(cache_hit_tokens + cache_miss_tokens, 1)
    elif cached_tokens is not None and prompt_tokens:
        cache_hit_rate = cached_tokens / max(prompt_tokens, 1)

    return ModelUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        cache_hit_rate=cache_hit_rate,
    )


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _first_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _int_or_none(mapping.get(key))
        if value is not None:
            return value
    return None


def _first_present_int(*values: Any) -> int | None:
    for value in values:
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
