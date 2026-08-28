import json

import httpx
import pytest

from minicc.core import provider as provider_module
from minicc.core.provider import (
    AUTH,
    BAD_REQUEST,
    CONTEXT_OVERFLOW,
    EMPTY_RESPONSE,
    QUOTA,
    RATE_LIMIT,
    SERVER,
    TIMEOUT,
    TRANSPORT,
    UNKNOWN,
    Backoff,
    CompletionOptions,
    LlmFailure,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderRegistry,
    RetryPolicy,
    extract_chat_text,
    extract_finish_reason,
    parse_model_usage,
    resolve_retry_policy,
)


def _mock_http_client(monkeypatch, handler):
    """Route every ``httpx.Client`` the provider creates through a MockTransport.

    ``OpenAICompatibleProvider`` builds its own ``httpx.Client`` lazily, so the
    deterministic way to intercept requests without a real network is to swap the
    ``Client`` constructor in the provider's module namespace for the duration of
    the test.
    """
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        provider_module.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    return transport


def _ok_response(text: str = '{"type":"final","answer":"done"}') -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    )


# --- usage / text / finish-reason parsing (unchanged contract) ----------------


def test_parse_deepseek_style_cache_usage() -> None:
    usage = parse_model_usage(
        {
            "prompt_tokens": 48000,
            "completion_tokens": 900,
            "total_tokens": 48900,
            "prompt_cache_hit_tokens": 42000,
            "prompt_cache_miss_tokens": 6000,
        }
    )

    assert usage.prompt_tokens == 48000
    assert usage.completion_tokens == 900
    assert usage.cache_hit_tokens == 42000
    assert usage.cache_miss_tokens == 6000
    assert usage.cached_tokens == 42000
    assert usage.cache_hit_rate == 0.875


def test_parse_openai_style_cached_tokens() -> None:
    usage = parse_model_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 250},
        }
    )

    assert usage.cached_tokens == 250
    assert usage.cache_hit_rate == 0.25


def test_extract_chat_text_from_openai_compatible_response() -> None:
    text = extract_chat_text(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"type":"final","answer":"done"}',
                    }
                }
            ]
        }
    )

    assert text == '{"type":"final","answer":"done"}'


def test_extract_finish_reason_non_stream() -> None:
    reason = extract_finish_reason(
        {"choices": [{"message": {"content": "..."}, "finish_reason": "length"}]}
    )

    assert reason == "length"


def test_extract_finish_reason_missing() -> None:
    assert extract_finish_reason({}) is None
    assert extract_finish_reason({"choices": []}) is None


def test_extract_finish_reason_stream_takes_last_non_null() -> None:
    reason = extract_finish_reason(
        {
            "chunks": [
                {"choices": [{"finish_reason": None}]},
                {"choices": [{"finish_reason": "length"}]},
            ]
        }
    )

    assert reason == "length"


# --- single-attempt completion via MockTransport ------------------------------


def test_complete_single_attempt_returns_response(monkeypatch) -> None:
    _mock_http_client(monkeypatch, lambda request: _ok_response())
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    response = provider.complete([])

    assert response.text == '{"type":"final","answer":"done"}'
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 10
    assert response.latency_ms >= 0


def test_complete_injects_json_mode_response_format(monkeypatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return _ok_response()

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    provider.complete([], options=CompletionOptions(json_mode=True))

    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["payload"]["stream"] is False
    assert seen["payload"]["temperature"] == 0.0


def test_complete_falls_back_when_json_mode_is_bad_request(monkeypatch) -> None:
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if "response_format" in payloads[-1]:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return _ok_response("text " + '{"type":"final","answer":"done"}')

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="legacy-model",
    )

    response = provider.complete([])

    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
    assert response.text.startswith("text ")


def test_complete_applies_completion_token_limit(monkeypatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return _ok_response()

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    provider.complete([], options=CompletionOptions(max_tokens=2048))

    assert seen["payload"]["max_tokens"] == 2048


# --- stable failure classification -------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected_code"),
    [
        (429, '{"error":{"message":"rate limited"}}', RATE_LIMIT),
        (401, '{"error":{"message":"bad key"}}', AUTH),
        (403, '{"error":{"message":"forbidden"}}', AUTH),
        (500, '{"error":{"message":"boom"}}', SERVER),
        (503, '{"error":{"message":"overloaded"}}', SERVER),
        (402, '{"error":{"message":"no money"}}', QUOTA),
        (409, '{"error":{"message":"insufficient quota"}}', QUOTA),  # quota signature on non-402
        (400, '{"error":{"message":"bad request"}}', BAD_REQUEST),
        (422, '{"error":{"message":"unprocessable"}}', BAD_REQUEST),
        (413, '{"error":{"message":"too big"}}', CONTEXT_OVERFLOW),
        (400, '{"error":{"message":"context length exceeded"}}', CONTEXT_OVERFLOW),
        (418, '{"error":{"message":"teapot"}}', UNKNOWN),
    ],
)
def test_complete_maps_http_status_to_failure_code(
    monkeypatch, status, body, expected_code
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body.encode())

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete([])

    failure = exc_info.value.failure
    assert failure.code == expected_code
    assert failure.status == status


def test_complete_429_captures_retry_after_ms(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": "rate limited"}, headers={"Retry-After": "3"}
        )

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete([])

    assert exc_info.value.failure.retry_after_ms == 3000


def test_complete_empty_completion_raises_empty_response(monkeypatch) -> None:
    _mock_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": ""}}]}
        ),
    )
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete([])

    assert exc_info.value.failure.code == EMPTY_RESPONSE


def test_complete_connect_error_is_transport(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete([])

    assert exc_info.value.failure.code == TRANSPORT


def test_complete_timeout_exception_is_timeout(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete([])

    assert exc_info.value.failure.code == TIMEOUT


def test_complete_non_dict_body_is_unknown(monkeypatch) -> None:
    _mock_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, content=b'"just a string"'),
    )
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete([])

    assert exc_info.value.failure.code == UNKNOWN


def test_complete_stream_aggregates_chunks(monkeypatch) -> None:
    def _sse(data: str) -> str:
        return f"data: {data}\n\n"

    stream_content = (
        _sse(json.dumps({"choices": [{"delta": {"content": '{"type":'}}]}))
        + _sse(json.dumps({"choices": [{"delta": {"content": '"final","answer":"done"}'}}]}))
        + _sse(
            json.dumps(
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
        )
        + _sse("[DONE]")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=stream_content.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

    _mock_http_client(monkeypatch, handler)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    deltas: list[str] = []
    response = provider.complete(
        [], options=CompletionOptions(stream=True, on_text_delta=deltas.append)
    )

    assert response.text == '{"type":"final","answer":"done"}'
    assert response.usage.total_tokens == 2
    assert "".join(deltas) == response.text


def test_retry_after_ms_explicit_header() -> None:
    assert provider_module._parse_retry_after_ms({"retry-after-ms": "1500"}) == 1500


def test_retry_after_ms_seconds() -> None:
    assert provider_module._parse_retry_after_ms({"Retry-After": "3"}) == 3000


# --- registry -------------------------------------------------------------


def test_provider_registry_build_and_get_caches_by_route(monkeypatch) -> None:
    _mock_http_client(monkeypatch, lambda request: _ok_response())
    registry = ProviderRegistry()

    first = registry.build(
        route_name="primary",
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        provider_name="My Primary",
    )
    second = registry.build(
        route_name="primary",
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        provider_name="My Primary",
    )

    assert first is second
    assert registry.get("primary") is first
    assert registry.get("missing") is None
    assert first.provider_name == "My Primary"


# --- retry policy resolution ------------------------------------------------


def test_resolve_retry_policy_defaults() -> None:
    policy = resolve_retry_policy(None)

    assert policy.mode == "normal"
    assert policy.max_retries == 2
    assert policy.retryable_codes == provider_module.TRANSIENT_CODES
    assert policy.backoff == Backoff()


def test_resolve_retry_policy_custom() -> None:
    policy = resolve_retry_policy(
        {
            "mode": "always",
            "max_retries": 5,
            "retryable_codes": [RATE_LIMIT, TIMEOUT],
            "backoff": {"initial_delay_ms": 100, "max_delay_ms": 1000, "jitter_ratio": 0.5},
        }
    )

    assert policy.mode == "always"
    assert policy.max_retries == 5
    assert policy.retryable_codes == (RATE_LIMIT, TIMEOUT)
    assert policy.backoff.initial_delay_ms == 100
    assert policy.backoff.jitter_ratio == 0.5


@pytest.mark.parametrize(
    "config",
    [
        {"unknown_key": 1},
        {"mode": "sometimes"},
        {"max_retries": -1},
        {"retryable_codes": ["NOT_A_CODE"]},
        {"retryable_codes": []},
        {"retryable_codes": [RATE_LIMIT, RATE_LIMIT]},
        {"backoff": {"initial_delay_ms": 1000, "max_delay_ms": 100}},
        {"backoff": {"jitter_ratio": 1.5}},
        {"backoff": {"nope": 1}},
    ],
)
def test_resolve_retry_policy_rejects_invalid(config) -> None:
    with pytest.raises(ValueError):
        resolve_retry_policy(config)


def test_retry_policy_default_instance() -> None:
    policy = RetryPolicy()
    assert policy.retryable_codes == provider_module.TRANSIENT_CODES


# --- LlmFailure / ProviderError shape ----------------------------------------


def test_provider_error_exposes_failure_code() -> None:
    failure = LlmFailure(message="boom", code=RATE_LIMIT, status=429)
    error = ProviderError(failure=failure)

    assert error.failure.code == RATE_LIMIT
    assert error.failure.status == 429
    assert str(error) == "boom"
    assert error.failure.to_dict()["code"] == RATE_LIMIT


def test_provider_error_message_override() -> None:
    failure = LlmFailure(message="boom", code=SERVER)
    error = ProviderError(failure=failure, message="cleaner message")

    assert str(error) == "cleaner message"
    assert error.failure.code == SERVER
