from __future__ import annotations

import pytest

from minicc.core.provider import (
    BAD_REQUEST,
    RATE_LIMIT,
    LlmFailure,
    ModelResponse,
    ModelUsage,
    NativeToolCall,
    ProviderError,
    RetryPolicy,
)
from minicc.core.retry import RetryingTurnProvider, _compute_delay, run_with_retry
from minicc.core.runner import ModelTurnRunner
from minicc.core.state import RunState
from minicc.trace.recorder import TraceRecorder


def _ok_response() -> ModelResponse:
    return ModelResponse(
        text="",
        raw={},
        usage=ModelUsage(prompt_tokens=1),
        latency_ms=1,
        tool_calls=(NativeToolCall(id="f1", name="final", arguments='{"answer":"done"}'),),
    )


def _failure(code: str, *, retry_after_ms: int | None = None) -> ProviderError:
    return ProviderError(
        failure=LlmFailure(message=f"{code} failure", code=code, retry_after_ms=retry_after_ms)
    )


class FlakyProvider:
    """Pops one step per ``complete`` call: an exception (re-raised) or a response."""

    def __init__(self, steps: list[object]) -> None:
        self._steps = list(steps)
        self.calls = 0

    def complete(self, messages, *, options=None) -> ModelResponse:
        self.calls += 1
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step  # type: ignore[return-value]


def _make_turn_provider(provider, policy, *, trace=None):
    runner = ModelTurnRunner(provider, trace=trace)
    return RetryingTurnProvider(
        runner,
        route_name="primary",
        provider=provider,
        policy=policy,
        trace=trace,
        sleep_fn=lambda _seconds: None,
        rng=lambda: 0.5,
    )


def test_retries_transient_then_succeeds() -> None:
    provider = FlakyProvider([_failure(RATE_LIMIT), _failure(RATE_LIMIT), _ok_response()])
    state = RunState.start("finish")
    trace = TraceRecorder()

    turn = _make_turn_provider(provider, RetryPolicy(max_retries=2), trace=trace).next_turn(
        state, [{"role": "user", "content": "hi"}]
    )

    assert len(turn.actions) == 1
    assert provider.calls == 3
    assert state.metrics["provider_request_attempts"] == 3
    assert state.metrics["provider_retried_requests"] == 1
    retry_events = [event for event in trace.events if event["event"] == "llm/retry"]
    assert [event["code"] for event in retry_events] == [RATE_LIMIT, RATE_LIMIT]
    assert [event["retry_index"] for event in retry_events] == [0, 1]


def test_non_transient_code_is_not_retried() -> None:
    provider = FlakyProvider([_failure(BAD_REQUEST)])
    state = RunState.start("finish")

    with pytest.raises(ProviderError) as excinfo:
        _make_turn_provider(provider, RetryPolicy()).next_turn(state, [])

    assert excinfo.value.failure.code == BAD_REQUEST
    assert provider.calls == 1
    # 失败 attempt 只在成功返回时累计，这里没有成功，指标停留在 baseline。
    assert state.metrics["provider_request_attempts"] == 0
    assert state.metrics["provider_retried_requests"] == 0


def test_exhausts_retries_then_raises_last_failure() -> None:
    provider = FlakyProvider([_failure(RATE_LIMIT), _failure(RATE_LIMIT), _failure(RATE_LIMIT)])
    state = RunState.start("finish")

    with pytest.raises(ProviderError):
        _make_turn_provider(provider, RetryPolicy(max_retries=2)).next_turn(state, [])

    assert provider.calls == 3
    assert state.metrics["provider_request_attempts"] == 0


def test_always_mode_retries_past_cap() -> None:
    steps = [_failure(RATE_LIMIT) for _ in range(5)] + [_ok_response()]
    provider = FlakyProvider(steps)
    state = RunState.start("finish")

    turn = _make_turn_provider(
        provider,
        RetryPolicy(mode="always", max_retries=1),
    ).next_turn(state, [])

    assert len(turn.actions) == 1
    assert provider.calls == 6
    assert state.metrics["provider_request_attempts"] == 6
    assert state.metrics["provider_retried_requests"] == 1


def test_attempt_metadata_recorded_on_success() -> None:
    provider = FlakyProvider([_failure(RATE_LIMIT), _ok_response()])
    state = RunState.start("finish")
    trace = TraceRecorder()

    _make_turn_provider(provider, RetryPolicy(max_retries=2), trace=trace).next_turn(state, [])

    model_responses = [event for event in trace.events if event["event"] == "model_response"]
    assert model_responses[0]["attempt_count"] == 2
    assert model_responses[0]["retry_reasons"] == [RATE_LIMIT]


def test_run_with_retry_function_accumulates_attempts() -> None:
    provider = FlakyProvider([_failure(RATE_LIMIT), _ok_response()])
    runner = ModelTurnRunner(provider)
    state = RunState.start("finish")

    run_with_retry(
        runner,
        state=state,
        messages=[],
        route_name="primary",
        provider=provider,
        policy=RetryPolicy(max_retries=1),
        sleep_fn=lambda _seconds: None,
        rng=lambda: 0.5,
    )

    assert provider.calls == 2
    assert state.metrics["provider_request_attempts"] == 2
    assert state.metrics["provider_retried_requests"] == 1


def test_compute_delay_retry_after_wins() -> None:
    assert _compute_delay(RetryPolicy(), 0, 1234, lambda: 0.5) == 1234


def test_compute_delay_retry_after_beyond_max_abandons_in_normal_mode() -> None:
    assert _compute_delay(RetryPolicy(), 0, 999_999, lambda: 0.5) is None


def test_compute_delay_always_mode_falls_back_to_backoff() -> None:
    # always 模式下超出上限的 Retry-After 不回 None，而是退化到本地指数退避。
    policy = RetryPolicy(mode="always")
    assert _compute_delay(policy, 0, 999_999, lambda: 0.5) == 500


def test_compute_delay_exponential_with_midpoint_rng() -> None:
    policy = RetryPolicy()  # initial 500, max 10000, jitter 0.1
    # rng=0.5 → jitter = 0.1 * (2*0.5 - 1) = 0 → base unchanged
    assert _compute_delay(policy, 0, None, lambda: 0.5) == 500
    assert _compute_delay(policy, 1, None, lambda: 0.5) == 1000
    assert _compute_delay(policy, 2, None, lambda: 0.5) == 2000
    assert _compute_delay(policy, 10, None, lambda: 0.5) == 10_000