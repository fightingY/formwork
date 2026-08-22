from __future__ import annotations

import random
import time
from collections.abc import Callable

from minicc.core.provider import ModelProvider, ProviderError, RetryPolicy
from minicc.core.runner import ModelTurn, ModelTurnRunner
from minicc.core.state import RunState
from minicc.trace.recorder import TraceRecorder


def run_with_retry(
    turn_runner: ModelTurnRunner,
    *,
    state: RunState,
    messages: list[dict[str, str]],
    route_name: str,
    provider: ModelProvider,
    policy: RetryPolicy,
    trace: TraceRecorder | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> ModelTurn:
    """Run one turn through ``provider``, retrying transient failures per-route.

    V4.1 把传输重试从 ``OpenAICompatibleProvider.complete()`` 上移到 agent loop 的
    失败步骤扩展点：每次 attempt 是 ``ModelTurnRunner.next_turn`` 的一次单次可见调用，
    ``LlmFailure.code`` 落在 ``policy.retryable_codes`` 且尚未耗尽时才退避重试。
    ``mode="always"`` 无上限重试（直到成功或遇到不可重试码）。失败 attempt 不提交
    assistant 消息、不虚增 turn 计数——只有成功的 attempt 走完 runner 的解析路径。

    成功返回时才把 attempt 计数灌进 ``state.metrics``：``provider_request_attempts``
    累加本回合的 attempt 数，``provider_retried_requests`` 仅在发生过重试时 +1。
    （与原实现一致：只有成功的 ModelResponse 才被计入，失败的尝试不产 ModelResponse。）
    """
    attempted = 0
    retry_reasons: list[str] = []
    while True:
        attempted += 1
        try:
            turn = turn_runner.next_turn(
                state,
                messages,
                provider=provider,
                attempt_count=attempted,
                retry_reasons=tuple(retry_reasons),
            )
        except ProviderError as exc:
            failure = exc.failure
            retryable = failure.code in policy.retryable_codes
            exhausted = policy.mode == "normal" and attempted > policy.max_retries
            if not retryable or exhausted:
                raise
            retry_reasons.append(failure.code)
            retry_index = len(retry_reasons) - 1
            delay_ms = _compute_delay(policy, retry_index, failure.retry_after_ms, rng)
            if delay_ms is None:
                # normal 模式下上游要求的 Retry-After 超出本地退避上限：放弃本 route。
                raise
            if trace is not None:
                trace.llm_retry(
                    state,
                    route=route_name,
                    code=failure.code,
                    retry_index=retry_index,
                    delay_ms=delay_ms,
                    failure=failure,
                )
            sleep_fn(delay_ms / 1000.0)
            continue

        state.metrics["provider_request_attempts"] = (
            int(state.metrics.get("provider_request_attempts", 0)) + attempted
        )
        if attempted > 1:
            state.metrics["provider_retried_requests"] = (
                int(state.metrics.get("provider_retried_requests", 0)) + 1
            )
        return turn


def _compute_delay(
    policy: RetryPolicy,
    retry_index: int,
    retry_after_ms: int | None,
    rng: Callable[[], float],
) -> int | None:
    """Compute the backoff delay before the ``retry_index``-th retry (0-based).

    An upstream ``Retry-After`` hint (already normalized to milliseconds) always
    wins when present: in ``normal`` mode a hint beyond ``backoff.max_delay_ms``
    signals "don't wait that long" and yields ``None`` so the caller abandons the
    route; ``always`` mode falls back to local exponential backoff instead. Without
    a hint, delay = ``initial * 2^retry_index`` (capped) plus ±jitter.
    """
    backoff = policy.backoff
    if retry_after_ms is not None and retry_after_ms > 0:
        if retry_after_ms > backoff.max_delay_ms and policy.mode == "normal":
            return None
        if retry_after_ms <= backoff.max_delay_ms:
            return retry_after_ms
        # always 模式 + 超出上限的 Retry-After：不放弃，退化到本地指数退避。
    base = min(
        backoff.initial_delay_ms * (2 ** max(int(retry_index), 0)),
        backoff.max_delay_ms,
    )
    jitter = backoff.jitter_ratio * (2.0 * rng() - 1.0)
    return max(0, int(round(base * (1.0 + jitter))))


class RetryingTurnProvider:
    """``TurnProvider`` that retries a single route on transient ``ProviderError``.

    The no-failover default: one adapter + its per-route ``RetryPolicy``, executed
    by :func:`run_with_retry` against the shared ``ModelTurnRunner``. ``sleep_fn`` /
    ``rng`` are injectable so tests can assert the retry sequence with no real sleep.
    """

    def __init__(
        self,
        runner: ModelTurnRunner,
        *,
        route_name: str,
        provider: ModelProvider,
        policy: RetryPolicy,
        trace: TraceRecorder | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._runner = runner
        self._route_name = route_name
        self._provider = provider
        self._policy = policy
        self._trace = trace
        self._sleep_fn = sleep_fn
        self._rng = rng

    def next_turn(self, state: RunState, messages: list[dict[str, str]]) -> ModelTurn:
        return run_with_retry(
            self._runner,
            state=state,
            messages=messages,
            route_name=self._route_name,
            provider=self._provider,
            policy=self._policy,
            trace=self._trace,
            sleep_fn=self._sleep_fn,
            rng=self._rng,
        )