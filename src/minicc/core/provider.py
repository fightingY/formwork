from __future__ import annotations

import email.utils
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

# --- Stable failure codes ----------------------------------------------------
#
# V4.1 合同（Provider 层多上游降级）：adapter 只上报「事实」——这次失败属于哪一类
# 稳定的失败码——而不决定「动作」（是否重试 / 是否降级到下一上游）。重试与降级的
# 决策由 policy 层（core/retry.py、core/failover.py）依据这些码做出。因此码的取值
# 属于公共合同：任何改动都要同步「黄金重试矩阵」与 failover 的 ``on`` 集合。

# 瞬态码：同一 route 上值得回退重试，也是默认 failover 触发集合。
RATE_LIMIT = "RATE_LIMIT"
SERVER = "SERVER"
TIMEOUT = "TIMEOUT"
TRANSPORT = "TRANSPORT"
EMPTY_RESPONSE = "EMPTY_RESPONSE"

# 非瞬态码：重试无意义或有害，直接放行给 failover 链或上层（Auth/Quota/协议错）。
AUTH = "AUTH"
QUOTA = "QUOTA"
BAD_REQUEST = "BAD_REQUEST"
CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
ABORTED = "ABORTED"
UNKNOWN = "UNKNOWN"

# 黄金重试矩阵里可重试的 5 个瞬态码（移植自 deepseek-harness 的 dsh-llm）。
TRANSIENT_CODES: tuple[str, ...] = (
    RATE_LIMIT,
    SERVER,
    TIMEOUT,
    TRANSPORT,
    EMPTY_RESPONSE,
)

ALL_CODES: frozenset[str] = frozenset(
    {*TRANSIENT_CODES, AUTH, QUOTA, BAD_REQUEST, CONTEXT_OVERFLOW, ABORTED, UNKNOWN}
)

# failover 链默认在哪些码上「跳去下一上游」：配额/认证的失败换一个上游通常能解决，
# 瞬态失败亦是如此。EMPTY_RESPONSE 刻意不在其中——它更倾向于是模型/路由的瞬时
# 空洞，retry 而非 failover 才是第一反应。
FAILOVER_DEFAULT_ON: tuple[str, ...] = (
    QUOTA,
    AUTH,
    RATE_LIMIT,
    SERVER,
    TIMEOUT,
    TRANSPORT,
)


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
    finish_reason: str | None = None


@dataclass(frozen=True)
class CompletionOptions:
    temperature: float = 0.0
    stream: bool = False
    include_usage: bool = True
    json_mode: bool = True
    max_tokens: int | None = None
    # Optional UI hook. The provider still returns one complete response for
    # protocol parsing; callers may use deltas for progressive display.
    on_text_delta: Callable[[str], None] | None = None


class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        ...


@dataclass(frozen=True)
class LlmFailure:
    """A fact describing one provider failure.

    Fields describe *what happened*, never *what to do next*: there is
    deliberately no ``retryable`` / ``failover`` flag here. Policy layers
    (retry / failover) map :attr:`code` onto their own action matrix.
    """

    message: str
    code: str = UNKNOWN
    status: int | None = None
    retry_after_ms: int | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "code": self.code,
            "status": self.status,
            "retry_after_ms": self.retry_after_ms,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class Backoff:
    """The "golden retry matrix" backoff numbers (ported, not copied verbatim)."""

    initial_delay_ms: int = 500
    max_delay_ms: int = 10_000
    jitter_ratio: float = 0.1


RetryMode = Literal["normal", "always"]


@dataclass(frozen=True)
class RetryPolicy:
    """Per-route retry contract applied by the failure-step retry executor.

    ``mode="always"`` means "retry this route forever" (used by some upstream
    integrations); ``"normal"`` respects ``max_retries``. ``retryable_codes`` is
    the set of transient codes worth retrying before ever failing over.
    """

    mode: RetryMode = "normal"
    max_retries: int = 2
    retryable_codes: tuple[str, ...] = TRANSIENT_CODES
    backoff: Backoff = field(default_factory=Backoff)


def resolve_retry_policy(config: Mapping[str, Any] | None) -> RetryPolicy:
    """Validate and normalize a ``retry_policy`` mapping into a RetryPolicy.

    Raises ``ValueError`` on unknown keys, invalid mode, invalid codes, or
    inconsistent backoff bounds — the caller turns those into fail-fast
    configuration errors.
    """
    if not config:
        return RetryPolicy()

    allowed = {"mode", "max_retries", "retryable_codes", "backoff"}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"retry_policy has unknown keys: {sorted(unknown)}")

    mode = config.get("mode", "normal")
    if mode not in ("normal", "always"):
        raise ValueError("retry_policy.mode must be 'normal' or 'always'")

    max_retries = config.get("max_retries", 2)
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("retry_policy.max_retries must be a non-negative integer")

    raw_codes = config.get("retryable_codes", list(TRANSIENT_CODES))
    if not isinstance(raw_codes, (list, tuple)) or not raw_codes:
        raise ValueError("retry_policy.retryable_codes must be a non-empty list")
    codes = tuple(str(item) for item in raw_codes)
    invalid = [code for code in codes if code not in ALL_CODES]
    if invalid:
        raise ValueError(f"retry_policy.retryable_codes has unknown codes: {invalid}")
    if len(set(codes)) != len(codes):
        raise ValueError("retry_policy.retryable_codes must not contain duplicates")

    backoff = Backoff()
    backoff_cfg = config.get("backoff")
    if backoff_cfg is not None:
        if not isinstance(backoff_cfg, Mapping):
            raise ValueError("retry_policy.backoff must be a mapping")
        unknown_backoff = set(backoff_cfg) - {"initial_delay_ms", "max_delay_ms", "jitter_ratio"}
        if unknown_backoff:
            raise ValueError(f"retry_policy.backoff has unknown keys: {sorted(unknown_backoff)}")
        initial = int(backoff_cfg.get("initial_delay_ms", backoff.initial_delay_ms))
        maximum = int(backoff_cfg.get("max_delay_ms", backoff.max_delay_ms))
        jitter = float(backoff_cfg.get("jitter_ratio", backoff.jitter_ratio))
        if initial <= 0 or maximum <= 0 or initial > maximum:
            raise ValueError(
                "retry_policy.backoff requires 0 < initial_delay_ms <= max_delay_ms"
            )
        if not 0.0 <= jitter <= 1.0:
            raise ValueError("retry_policy.backoff.jitter_ratio must be within [0, 1]")
        backoff = Backoff(
            initial_delay_ms=initial,
            max_delay_ms=maximum,
            jitter_ratio=jitter,
        )

    return RetryPolicy(
        mode=mode,
        max_retries=max_retries,
        retryable_codes=codes,
        backoff=backoff,
    )


class ProviderError(RuntimeError):
    """Expected failure while communicating with a model provider.

    The single source of truth is :attr:`failure` (an :class:`LlmFailure`); the
    optional ``message`` overrides the human-readable string only.
    """

    def __init__(self, *, failure: LlmFailure, message: str | None = None) -> None:
        self.failure = failure
        super().__init__(message if message is not None else failure.message)


_QUOTA_SIGNATURES = (
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "quota has been exhausted",
    "billing",
)

_CONTEXT_OVERFLOW_SIGNATURES = (
    "context length",
    "context_length",
    "maximum context length",
    "context window",
    "prompt is too long",
    "tokens exceed",
    "maximum context",
    "context_length_exceeded",
)


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible chat completions APIs (single attempt).

    V4.1 把一个回合收敛为**单次可见 attempt**：传输重试被移出本适配器，改由
    ``core/retry.run_with_retry`` 在 agent loop 的失败步骤扩展点上按 route 政策执行。
    本类只负责发一次请求、把失败归一成 :class:`LlmFailure` 编码的 ``ProviderError``。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_ms: int = 120_000,
        headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        stream_idle_timeout_ms: int = 300_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_ms = max(0, timeout_ms)
        self.timeout_sec = self.timeout_ms / 1000
        # The stream leg gets its own (longer) watchdog ceiling: a slowly-trickling
        # stream should not be cut off at the non-stream total timeout.
        self.stream_idle_timeout_ms = max(0, stream_idle_timeout_ms)
        self.stream_idle_timeout_sec = self.stream_idle_timeout_ms / 1000
        self.extra_headers = dict(headers or {})
        self.provider_name = provider_name or _provider_name(base_url, model)
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
        try:
            raw, text, usage_raw = self._request_once(
                payload, stream=options.stream, on_text_delta=options.on_text_delta
            )
        except ProviderError as exc:
            if (
                options.json_mode
                and "response_format" in payload
                and exc.failure.code == BAD_REQUEST
            ):
                # Some OpenAI-compatible providers or model variants do not
                # implement native JSON mode. Fall back — within this same
                # visible attempt — to local extraction, without a config edit.
                payload.pop("response_format", None)
                raw, text, usage_raw = self._request_once(
                    payload, stream=options.stream, on_text_delta=options.on_text_delta
                )
            else:
                raise

        if not text.strip():
            raise ProviderError(
                failure=LlmFailure(
                    message="Provider returned an empty completion",
                    code=EMPTY_RESPONSE,
                )
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        return ModelResponse(
            text=text,
            raw=raw,
            usage=parse_model_usage(usage_raw),
            latency_ms=latency_ms,
            finish_reason=extract_finish_reason(raw),
        )

    def _request_once(
        self,
        payload: dict[str, Any],
        *,
        stream: bool,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, Any], str, Mapping[str, Any] | None]:
        if stream:
            return self._complete_stream(payload, on_text_delta=on_text_delta)
        raw = self._post_json(payload)
        usage_raw = raw.get("usage") if isinstance(raw, dict) else None
        return raw, extract_chat_text(raw), usage_raw

    def _headers(self) -> dict[str, str]:
        result = dict(self.extra_headers)
        result["Authorization"] = f"Bearer {self.api_key}"
        result["Content-Type"] = "application/json"
        return result

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        data: Any
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
                    failure=LlmFailure(
                        message=f"Provider request exceeded timeout of {self.timeout_sec:g}s",
                        code=TIMEOUT,
                    )
                )
        except httpx.HTTPStatusError as exc:
            raise ProviderError(failure=_classify_http_status(exc.response)) from exc
        except httpx.HTTPError as exc:
            self._discard_client(client)
            if timed_out:
                raise ProviderError(
                    failure=LlmFailure(
                        message=f"Provider request exceeded timeout of {self.timeout_sec:g}s",
                        code=TIMEOUT,
                    )
                ) from exc
            raise ProviderError(
                failure=LlmFailure(
                    message=f"Provider HTTP request failed: {type(exc).__name__}",
                    code=TIMEOUT if isinstance(exc, httpx.TimeoutException) else TRANSPORT,
                )
            ) from exc
        finally:
            watchdog.cancel()
            if timed_out:
                self._discard_client(client)
        if not isinstance(data, dict):
            raise ProviderError(
                failure=LlmFailure(
                    message="Provider response was not a JSON object.",
                    code=UNKNOWN,
                )
            )
        return data

    def _complete_stream(
        self,
        payload: dict[str, Any],
        *,
        on_text_delta: Callable[[str], None] | None = None,
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

        watchdog = threading.Timer(self.stream_idle_timeout_sec, close_client)
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
                            text_piece = str(piece)
                            content_parts.append(text_piece)
                            if on_text_delta is not None:
                                on_text_delta(text_piece)
            if timed_out:
                raise ProviderError(
                    failure=LlmFailure(
                        message=(
                            f"Provider stream exceeded timeout of "
                            f"{self.stream_idle_timeout_sec:g}s"
                        ),
                        code=TIMEOUT,
                    )
                )
        except httpx.HTTPStatusError as exc:
            raise ProviderError(failure=_classify_http_status(exc.response)) from exc
        except httpx.HTTPError as exc:
            self._discard_client(client)
            if timed_out:
                raise ProviderError(
                    failure=LlmFailure(
                        message=(
                            f"Provider stream exceeded timeout of "
                            f"{self.stream_idle_timeout_sec:g}s"
                        ),
                        code=TIMEOUT,
                    )
                ) from exc
            raise ProviderError(
                failure=LlmFailure(
                    message=f"Provider HTTP request failed: {type(exc).__name__}",
                    code=TIMEOUT if isinstance(exc, httpx.TimeoutException) else TRANSPORT,
                )
            ) from exc
        finally:
            watchdog.cancel()
            if timed_out:
                self._discard_client(client)

        return {"chunks": chunks, "usage": usage_raw}, "".join(content_parts), usage_raw


def _body_preview(body: str, limit: int = 500) -> str:
    """Collapse and bound a provider error body for safe inclusion in a log line."""
    collapsed = " ".join((body or "").split())
    if not collapsed:
        return "no response body"
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


def _classify_http_status(response: httpx.Response) -> LlmFailure:
    """Map a non-2xx HTTP response onto a stable failure code.

    The message keeps the HTTP status and a truncated response body so operators
    can tell 402 (quota) / 429 (rate limit) / 401·403 (auth) / 5xx (server) apart
    without re-running the provider.
    """
    status = response.status_code
    body = response.text
    code = _http_code(status, body)
    return LlmFailure(
        message=f"Provider HTTP request failed with status {status}: {_body_preview(body)}",
        code=code,
        status=status,
        retry_after_ms=_parse_retry_after_ms(response.headers),
        request_id=_extract_request_id(response.headers),
    )


def _http_code(status: int, body: str) -> str:
    if status in (401, 403):
        return AUTH
    if status == 402 or _has_quota_signature(body):
        return QUOTA
    if status == 429:
        return RATE_LIMIT
    if status == 413 or _has_context_overflow_signature(body):
        return CONTEXT_OVERFLOW
    if status >= 500:
        return SERVER
    if status in (400, 422):
        return BAD_REQUEST
    return UNKNOWN


def _has_quota_signature(body: str) -> bool:
    lowered = (body or "").lower()
    return any(signature in lowered for signature in _QUOTA_SIGNATURES)


def _has_context_overflow_signature(body: str) -> bool:
    lowered = (body or "").lower()
    return any(signature in lowered for signature in _CONTEXT_OVERFLOW_SIGNATURES)


def _parse_retry_after_ms(headers: Mapping[str, str]) -> int | None:
    """Parse a retry-after hint into milliseconds, or ``None``.

    Prefers an explicit ``retry-after-ms`` header; falls back to the HTTP
    ``Retry-After`` value (seconds or HTTP-date).
    """
    explicit = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
    if explicit:
        try:
            return max(0, int(str(explicit).strip()))
        except (TypeError, ValueError):
            pass

    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is None:
        return None
    value = str(retry_after).strip()
    if not value:
        return None
    try:
        return max(0, int(float(value) * 1000))
    except ValueError:
        pass
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is not None:
        delta_ms = int((parsed.timestamp() - time.time()) * 1000)
        return max(0, delta_ms)
    return None


def _extract_request_id(headers: Mapping[str, str]) -> str | None:
    for key in ("x-request-id", "x-trace-id", "cf-ray", "x-ratelimit-request-id"):
        value = headers.get(key)
        if value:
            return str(value)
    return None


class ProviderRegistry:
    """Build and cache OpenAI-compatible adapters by route name.

    Deliberately config-agnostic: it accepts plain per-route fields, so
    ``config.py`` can import ``RetryPolicy``/``Backoff`` from this module without
    a cycle, while ``cli.py`` turns a ``ProviderRoute`` into these fields and
    calls :meth:`build`.
    """

    def __init__(self) -> None:
        self._providers: dict[str, OpenAICompatibleProvider] = {}

    def register(self, name: str, provider: OpenAICompatibleProvider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> OpenAICompatibleProvider | None:
        return self._providers.get(name)

    def build(
        self,
        *,
        route_name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_ms: int = 120_000,
        headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        stream_idle_timeout_ms: int = 300_000,
    ) -> OpenAICompatibleProvider:
        existing = self._providers.get(route_name)
        if existing is not None:
            return existing
        provider = OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_ms=timeout_ms,
            headers=headers,
            provider_name=provider_name,
            stream_idle_timeout_ms=stream_idle_timeout_ms,
        )
        self._providers[route_name] = provider
        return provider


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


def extract_finish_reason(raw: Mapping[str, Any]) -> str | None:
    chunks = raw.get("chunks")
    if isinstance(chunks, list):
        reason: str | None = None
        for item in chunks:
            if not isinstance(item, Mapping):
                continue
            for choice in item.get("choices") or []:
                if isinstance(choice, Mapping) and choice.get("finish_reason") is not None:
                    reason = choice.get("finish_reason")
        return reason
    choices = raw.get("choices") or []
    if choices and isinstance(choices[0], Mapping):
        return choices[0].get("finish_reason")
    return None


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


def _provider_name(base_url: str, model: str) -> str:
    identity = f"{base_url} {model}".lower()
    for name, markers in (
        ("deepseek", ("deepseek",)),
        ("anthropic", ("anthropic", "claude")),
        ("openai", ("openai.com", "gpt-", "codex")),
    ):
        if any(marker in identity for marker in markers):
            return name
    return urlparse(base_url).hostname or "openai-compatible"
