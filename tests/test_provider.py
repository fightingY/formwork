import httpx
import pytest

from minicc.core.provider import (
    CompletionOptions,
    OpenAICompatibleProvider,
    ProviderError,
    extract_chat_text,
    extract_finish_reason,
    parse_model_usage,
)


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


def test_provider_retries_transient_request_failures(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )
    calls = 0

    def fake_post_json(payload):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ProviderError("transient timeout")
        return {
            "choices": [{"message": {"content": '{"type":"final","answer":"done"}'}}],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post_json", fake_post_json)
    monkeypatch.setattr("minicc.core.provider.time.sleep", lambda seconds: None)

    response = provider.complete([])

    assert calls == 3
    assert response.text == '{"type":"final","answer":"done"}'


def test_provider_retries_empty_completion(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        max_retries=1,
    )
    calls = 0

    def empty_then_valid(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "choices": [{"message": {"content": ""}}],
                "usage": {"completion_tokens": 9},
            }
        return {
            "choices": [
                {"message": {"content": '{"type":"final","answer":"done"}'}}
            ],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post_json", empty_then_valid)
    monkeypatch.setattr("minicc.core.provider.time.sleep", lambda seconds: None)

    response = provider.complete([])

    assert calls == 2
    assert response.attempt_count == 2
    assert response.retry_reasons == ("empty_completion",)
    assert response.text == '{"type":"final","answer":"done"}'


def test_provider_prefers_native_json_mode(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.siliconflow.cn/v1",
        api_key="key",
        model="deepseek-ai/DeepSeek-V4-Flash",
    )
    payloads = []

    def fake_post_json(payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": '{"type":"final","answer":"done"}'}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post_json)

    provider.complete([], options=CompletionOptions(json_mode=True))

    assert payloads[0]["response_format"] == {"type": "json_object"}


def test_provider_applies_completion_token_limit(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )
    payloads = []

    def fake_post_json(payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": '{"summary":"short"}'}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post_json)

    provider.complete([], options=CompletionOptions(max_tokens=2048))

    assert payloads[0]["max_tokens"] == 2048


def test_provider_retries_after_absolute_timeout(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )
    calls = []

    attempts = 0

    def timed_out_twice(payload):
        nonlocal attempts
        calls.append(payload)
        attempts += 1
        if attempts < 3:
            raise ProviderError("deadline", timeout=True)
        return {"choices": [{"message": {"content": '{"type":"final","answer":"done"}'}}]}

    monkeypatch.setattr(provider, "_post_json", timed_out_twice)
    monkeypatch.setattr("minicc.core.provider.time.sleep", lambda seconds: None)

    response = provider.complete([])

    assert response.text == '{"type":"final","answer":"done"}'
    assert len(calls) == 3


def test_provider_honors_retry_limit(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        max_retries=1,
    )
    calls = []

    def timed_out(payload):
        calls.append(payload)
        raise ProviderError("deadline", timeout=True)

    monkeypatch.setattr(provider, "_post_json", timed_out)
    monkeypatch.setattr("minicc.core.provider.time.sleep", lambda seconds: None)

    with pytest.raises(ProviderError, match="deadline"):
        provider.complete([])

    assert len(calls) == 2


def test_provider_falls_back_when_native_json_mode_is_unsupported(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="legacy-model",
    )
    payloads = []

    def fake_post_json(payload):
        payloads.append(dict(payload))
        if "response_format" in payload:
            raise ProviderError("unsupported response_format", status_code=400)
        return {"choices": [{"message": {"content": 'text {"type":"final","answer":"done"}'}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post_json)

    response = provider.complete([])

    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
    assert response.text.startswith("text ")


def test_provider_reuses_one_http_client_within_session(monkeypatch) -> None:
    clients = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"type":"final","answer":"done"}'}}],
                "usage": {},
            }

    class FakeClient:
        def __init__(self, *, timeout):
            self.timeout = timeout
            self.closed = False
            self.posts = 0
            clients.append(self)

        def post(self, *args, **kwargs):
            self.posts += 1
            return FakeResponse()

        def close(self):
            self.closed = True

    monkeypatch.setattr("minicc.core.provider.httpx.Client", FakeClient)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    provider.start_session("run-1")
    provider.complete([])
    provider.complete([])

    assert len(clients) == 1
    assert clients[0].posts == 2
    provider.close()
    assert clients[0].closed is True


def test_provider_discards_broken_persistent_client_before_retry(monkeypatch) -> None:
    clients = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"type":"final","answer":"done"}'}}],
                "usage": {},
            }

    class FakeClient:
        def __init__(self, *, timeout):
            self.closed = False
            clients.append(self)

        def post(self, *args, **kwargs):
            if self is clients[0]:
                raise httpx.ConnectError(
                    "stale pooled connection",
                    request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                )
            return FakeResponse()

        def close(self):
            self.closed = True

    monkeypatch.setattr("minicc.core.provider.httpx.Client", FakeClient)
    monkeypatch.setattr("minicc.core.provider.time.sleep", lambda seconds: None)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        max_retries=1,
    )

    response = provider.complete([])

    assert response.text == '{"type":"final","answer":"done"}'
    assert len(clients) == 2
    assert clients[0].closed is True


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


def test_provider_surfaces_finish_reason(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
    )

    monkeypatch.setattr(
        provider,
        "_post_json",
        lambda payload: {
            "choices": [
                {"message": {"content": '{"type":"final","answer":"done"}'}, "finish_reason": "stop"}
            ],
            "usage": {},
        },
    )

    response = provider.complete([])

    assert response.finish_reason == "stop"
