from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

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
    attempt_count: int = 1
    retry_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionOptions:
    temperature: float = 0.0
    stream: bool = False
    include_usage: bool = True
    json_mode: bool = True
    max_tokens: int | None = None


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

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.timeout = timeout


def _retry_reason(error: ProviderError) -> str:
    if error.timeout:
        return "timeout"
    if error.status_code is not None:
        return f"http_status_{error.status_code}"
    if "empty completion" in str(error).lower():
        return "empty_completion"
    return "provider_transport_or_protocol_error"


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible chat completions APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float = 120,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec
        self.max_retries = max(0, max_retries)
        self._client: httpx.Client | None = None
        self._client_lock = threading.RLock()
        self._session_id = ""

    def start_session(self, session_id: str) -> None:
        normalized = str(session_id or "").strip()
        with self._client_lock:
            if normalized == self._session_id and self._client is not None:
                return
            self._close_client_locked()
            self._session_id = normalized
            self._client = httpx.Client(timeout=self.timeout_sec)

    def close(self) -> None:
        with self._client_lock:
            self._close_client_locked()

    def _close_client_locked(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()

    def _request_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self.timeout_sec)
            return self._client

    def _discard_client(self, client: httpx.Client) -> None:
        owned = False
        with self._client_lock:
            if self._client is client:
                self._client = None
                owned = True
        if owned:
            client.close()

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
        if options.max_tokens is not None and options.max_tokens > 0:
            payload["max_tokens"] = options.max_tokens
        if options.stream and options.include_usage:
            payload["stream_options"] = {"include_usage": True}
        if options.json_mode:
            # Prefer the provider's native structured-output mode. Local
            # protocol validation still handles truncated responses.
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        last_error: ProviderError | None = None
        retry_reasons: list[str] = []
        for attempt in range(self.max_retries + 1):
            try:
                if options.stream:
                    raw, text, usage_raw = self._complete_stream(payload)
                else:
                    raw = self._post_json(payload)
                    text = extract_chat_text(raw)
                    usage_raw = raw.get("usage") if isinstance(raw, dict) else None
                if not text.strip():
                    self.close()
                    raise ProviderError("Provider returned an empty completion")
                break
            except ProviderError as exc:
                last_error = exc
                retry_reasons.append(_retry_reason(exc))
                if "response_format" in payload and exc.status_code in {400, 422}:
                    # Some OpenAI-compatible providers or model variants do not
                    # implement native JSON mode. Fall back to local extraction
                    # and schema validation without requiring configuration edits.
                    payload.pop("response_format", None)
                    continue
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(8.0, 1.0 * (2**attempt)))
        else:
            raise last_error or ProviderError("Provider request failed")

        latency_ms = int((time.perf_counter() - started) * 1000)
        return ModelResponse(
            text=text,
            raw=raw,
            usage=parse_model_usage(usage_raw),
            latency_ms=latency_ms,
            attempt_count=attempt + 1,
            retry_reasons=tuple(retry_reasons),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        timed_out = False
        client = self._request_client()

        def close_client() -> None:
            nonlocal timed_out
            timed_out = True
            client.close()

        watchdog = threading.Timer(self.timeout_sec, close_client)
        watchdog.daemon = True
        try:
            watchdog.start()
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if timed_out:
                raise ProviderError(
                    f"Provider request exceeded timeout of {self.timeout_sec:g}s",
                    timeout=True,
                )
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Provider HTTP request failed: {type(exc).__name__}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            self._discard_client(client)
            if timed_out:
                raise ProviderError(
                    f"Provider request exceeded timeout of {self.timeout_sec:g}s",
                    timeout=True,
                ) from exc
            raise ProviderError(f"Provider HTTP request failed: {type(exc).__name__}") from exc
        finally:
            watchdog.cancel()
            if timed_out:
                self._discard_client(client)
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
        timed_out = False
        client = self._request_client()

        def close_client() -> None:
            nonlocal timed_out
            timed_out = True
            client.close()

        watchdog = threading.Timer(self.timeout_sec, close_client)
        watchdog.daemon = True

        try:
            watchdog.start()
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
            if timed_out:
                raise ProviderError(
                    f"Provider request exceeded timeout of {self.timeout_sec:g}s",
                    timeout=True,
                )
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Provider HTTP request failed: {type(exc).__name__}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            self._discard_client(client)
            if timed_out:
                raise ProviderError(
                    f"Provider request exceeded timeout of {self.timeout_sec:g}s",
                    timeout=True,
                ) from exc
            raise ProviderError(f"Provider HTTP request failed: {type(exc).__name__}") from exc
        finally:
            watchdog.cancel()
            if timed_out:
                self._discard_client(client)

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
