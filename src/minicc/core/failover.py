from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence

from minicc.core.provider import ModelProvider, ProviderError, RetryPolicy
from minicc.core.retry import run_with_retry
from minicc.core.runner import ModelTurn, ModelTurnRunner
from minicc.core.state import RunState
from minicc.trace.recorder import TraceRecorder


class ProviderFailoverChain:
    """Outermost upstream fallback: hop across routes when ``LlmFailure.code`` is in ``on``.

    V4.1 把「跨 route 的路由选择权放在最外层调度器」：本类消费 :class:`LlmFailure`
    契约做重路由，而 ``OpenAICompatibleProvider`` / ``ProviderRegistry`` 内部没有任何
    routing / failover 分支。每条 route 先走自己的 :func:`run_with_retry`（retry 从 0 计数），
    耗尽后若失败码命中 ``on`` 且未到链路末端、未超 ``max_hops``，则记一次 ``failover/hop``
    并切到下一 route；否则向上抛出，交给 agent loop 的 ``except ProviderError`` 终止 run。

    ``max_hops`` 是允许的降级跳转次数上限：``0``（默认）表示不限，可用完整链路
    （受链长自然约束）；正值则最多跳 ``max_hops`` 次后放弃。
    """

    def __init__(
        self,
        runner: ModelTurnRunner,
        *,
        routes: Sequence[tuple[str, ModelProvider, RetryPolicy]],
        on: tuple[str, ...],
        max_hops: int = 0,
        trace: TraceRecorder | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if not routes:
            raise ValueError("ProviderFailoverChain requires at least one route")
        self._runner = runner
        self._routes = list(routes)
        self._on = tuple(on)
        self._max_hops = max_hops
        self._trace = trace
        self._sleep_fn = sleep_fn
        self._rng = rng

    def next_turn(self, state: RunState, messages: list[dict[str, str]]) -> ModelTurn:
        hops = 0
        for index, (route_name, provider, policy) in enumerate(self._routes):
            try:
                return run_with_retry(
                    self._runner,
                    state=state,
                    messages=messages,
                    route_name=route_name,
                    provider=provider,
                    policy=policy,
                    trace=self._trace,
                    sleep_fn=self._sleep_fn,
                    rng=self._rng,
                )
            except ProviderError as exc:
                code = exc.failure.code
                next_index = index + 1
                at_end = next_index >= len(self._routes)
                capped = self._max_hops > 0 and hops >= self._max_hops
                if at_end or code not in self._on or capped:
                    raise
                if self._trace is not None:
                    self._trace.failover_hop(
                        state,
                        from_route=route_name,
                        to_route=self._routes[next_index][0],
                        code=code,
                    )
                hops += 1
        # Unreachable: every iteration either returns or raises.
        raise AssertionError("failover chain exhausted without a result")  # pragma: no cover