from __future__ import annotations

import pytest

from minicc.core.failover import ProviderFailoverChain
from minicc.core.provider import (
    AUTH,
    BAD_REQUEST,
    QUOTA,
    RATE_LIMIT,
    LlmFailure,
    ModelResponse,
    ModelUsage,
    NativeToolCall,
    ProviderError,
    RetryPolicy,
)
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


class AlwaysOk:
    provider_name = "backup"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *, options=None) -> ModelResponse:
        self.calls += 1
        return _ok_response()


class AlwaysFail:
    def __init__(self, code: str, name: str = "primary") -> None:
        self.code = code
        self.provider_name = name
        self.calls = 0

    def complete(self, messages, *, options=None) -> ModelResponse:
        self.calls += 1
        raise ProviderError(failure=LlmFailure(message=f"{self.code} failure", code=self.code))


def _chain(routes, *, on, max_hops=0, trace=None):
    return ProviderFailoverChain(
        ModelTurnRunner(routes[0][1]),
        routes=routes,
        on=on,
        max_hops=max_hops,
        trace=trace,
        sleep_fn=lambda _seconds: None,
        rng=lambda: 0.5,
    )


def test_failover_hops_to_second_route() -> None:
    primary = AlwaysFail(QUOTA, name="primary")
    backup = AlwaysOk()
    state = RunState.start("finish")
    trace = TraceRecorder()

    turn = _chain(
        [("primary", primary, RetryPolicy()), ("backup", backup, RetryPolicy())],
        on=(QUOTA,),
        trace=trace,
    ).next_turn(state, [])

    assert len(turn.actions) == 1
    assert primary.calls == 1
    assert backup.calls == 1
    hops = [event for event in trace.events if event["event"] == "failover/hop"]
    assert len(hops) == 1
    assert hops[0]["from_route"] == "primary"
    assert hops[0]["to_route"] == "backup"
    assert hops[0]["code"] == QUOTA
    # 降级后 runner 按实际 adapter 覆盖 provider 标识。
    assert state.metrics["provider_name"] == "backup"


def test_non_on_code_does_not_failover() -> None:
    primary = AlwaysFail(BAD_REQUEST, name="primary")
    backup = AlwaysOk()
    state = RunState.start("finish")
    trace = TraceRecorder()

    with pytest.raises(ProviderError) as excinfo:
        _chain(
            [("primary", primary, RetryPolicy()), ("backup", backup, RetryPolicy())],
            on=(QUOTA, AUTH),
            trace=trace,
        ).next_turn(state, [])

    assert excinfo.value.failure.code == BAD_REQUEST
    assert backup.calls == 0
    assert not [event for event in trace.events if event["event"] == "failover/hop"]


def test_max_hops_caps_failover() -> None:
    r1 = AlwaysFail(QUOTA, name="r1")
    r2 = AlwaysFail(QUOTA, name="r2")
    r3 = AlwaysOk()
    state = RunState.start("finish")
    trace = TraceRecorder()

    with pytest.raises(ProviderError):
        _chain(
            [("r1", r1, RetryPolicy()), ("r2", r2, RetryPolicy()), ("r3", r3, RetryPolicy())],
            on=(QUOTA,),
            max_hops=1,
            trace=trace,
        ).next_turn(state, [])

    assert r1.calls == 1
    assert r2.calls == 1
    assert r3.calls == 0
    assert len([event for event in trace.events if event["event"] == "failover/hop"]) == 1


def test_empty_routes_rejected() -> None:
    with pytest.raises(ValueError):
        ProviderFailoverChain(ModelTurnRunner(AlwaysOk()), routes=[], on=(QUOTA,))


def test_each_route_retries_then_fails_over() -> None:
    # 第一条 route 在 RATE_LIMIT 上重试一次后仍失败 → 降级到第二条成功。
    class FailTwice:
        provider_name = "primary"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, *, options=None) -> ModelResponse:
            self.calls += 1
            raise ProviderError(
                failure=LlmFailure(message="rate limited", code=RATE_LIMIT)
            )

    primary = FailTwice()
    backup = AlwaysOk()
    state = RunState.start("finish")
    trace = TraceRecorder()

    turn = _chain(
        [("primary", primary, RetryPolicy(max_retries=1)), ("backup", backup, RetryPolicy())],
        on=(RATE_LIMIT,),
        trace=trace,
    ).next_turn(state, [])

    assert len(turn.actions) == 1
    assert primary.calls == 2  # 1 + 1 retry，route 级重试从 0 计数
    assert backup.calls == 1
    retry_events = [event for event in trace.events if event["event"] == "llm/retry"]
    assert len(retry_events) == 1  # 只有 primary route 内部那一次重试