from dataclasses import dataclass

from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.runner import ModelTurnConfig, ModelTurnRunner
from minicc.core.state import RunState
from minicc.trace.recorder import TraceRecorder


@dataclass
class FakeProvider:
    responses: list[str]

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            text=self.responses.pop(0),
            raw={},
            usage=ModelUsage(prompt_tokens=3, completion_tokens=2),
            latency_ms=5,
        )


def test_model_turn_runner_parses_action_and_records_usage() -> None:
    state = RunState.start("finish")
    runner = ModelTurnRunner(FakeProvider(['{"type":"final","answer":"done"}']))

    turn = runner.next_turn(state, [{"role": "user", "content": "finish"}])

    assert turn.action is not None
    assert turn.observation is None
    assert state.metrics["turns"] == 1
    assert state.metrics["prompt_tokens"] == 3
    assert state.metrics["completion_tokens"] == 2
    # attempt 指标从 runner 迁到 retry 执行器（core/retry.py）：此处不再累计，
    # 仍停留在 RunState 基线的默认值 0。
    assert state.metrics["provider_request_attempts"] == 0
    assert state.metrics["provider_retried_requests"] == 0


def test_model_turn_runner_records_response_identity() -> None:
    class IdentityProvider:
        def complete(self, messages, *, options=None):
            return ModelResponse(
                text='{"type":"final","answer":"done"}',
                raw={"model": "actual-model", "system_fingerprint": "backend-1"},
                usage=ModelUsage(prompt_tokens=3),
                latency_ms=5,
            )

    state = RunState.start("finish")
    ModelTurnRunner(IdentityProvider()).next_turn(state, [])

    assert state.metrics["provider_response_models"] == ["actual-model"]
    assert state.metrics["provider_system_fingerprints"] == ["backend-1"]


def test_model_turn_runner_passes_attempt_metadata_through_to_trace() -> None:
    state = RunState.start("finish")
    trace = TraceRecorder()
    ModelTurnRunner(
        FakeProvider(['{"type":"final","answer":"done"}']),
        trace=trace,
    ).next_turn(state, [], attempt_count=2, retry_reasons=("timeout",))

    assert trace.events[0]["attempt_count"] == 2
    assert trace.events[0]["retry_reasons"] == ["timeout"]


def test_model_turn_runner_defaults_attempt_metadata_to_single() -> None:
    state = RunState.start("finish")
    trace = TraceRecorder()
    ModelTurnRunner(FakeProvider(['{"type":"final","answer":"done"}']), trace=trace).next_turn(
        state, []
    )

    assert trace.events[0]["attempt_count"] == 1
    assert trace.events[0]["retry_reasons"] == []


def test_model_turn_runner_stops_after_protocol_error_limit() -> None:
    state = RunState.start("bad")
    runner = ModelTurnRunner(
        FakeProvider(["bad", "bad"]),
        config=ModelTurnConfig(max_protocol_errors=1),
    )

    first = runner.next_turn(state, [{"role": "user", "content": "bad"}])
    second = runner.next_turn(state, [{"role": "user", "content": "bad again"}])

    assert first.should_continue is True
    assert first.observation is not None
    assert second.should_continue is False
    assert state.status == "failed"


@dataclass
class CacheProvider:
    usages: list[ModelUsage]

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            text='{"type":"bash","command":"true"}',
            raw={},
            usage=self.usages.pop(0),
            latency_ms=1,
        )


def test_model_turn_runner_uses_weighted_run_cache_hit_rate() -> None:
    state = RunState.start("cache")
    runner = ModelTurnRunner(
        CacheProvider(
            [
                ModelUsage(
                    prompt_tokens=1000,
                    cached_tokens=250,
                    cache_hit_tokens=250,
                    cache_miss_tokens=750,
                    cache_hit_rate=0.25,
                ),
                ModelUsage(
                    prompt_tokens=100,
                    cached_tokens=0,
                    cache_hit_tokens=0,
                    cache_miss_tokens=100,
                    cache_hit_rate=0.0,
                ),
            ]
        )
    )

    runner.next_turn(state, [])
    runner.next_turn(state, [])

    assert state.metrics["cache_metrics_available"] is True
    assert state.metrics["cache_metric_requests"] == 2
    assert state.metrics["cache_unreported_requests"] == 0
    assert state.metrics["cache_observed_hit_tokens"] == 250
    assert state.metrics["cache_observed_prompt_tokens"] == 1100
    assert state.metrics["cache_hit_rate"] == 250 / 1100
    assert state.metrics["cache_requests"] == [
        {
            "request_index": 0,
            "provider": "CacheProvider",
            "model": None,
            "prompt_tokens": 1000,
            "completion_tokens": None,
            "cache_read_tokens": 250,
            "cache_write_tokens": None,
            "uncached_tokens": 750,
            "cache_hit_rate": 0.25,
            "system_prefix_estimated_tokens": None,
            "project_prefix_estimated_tokens": None,
            "conversation_estimated_tokens": None,
            "prefix_hash": "",
            "previous_request_is_prefix": False,
            "longest_common_prefix_estimated_tokens": 0,
            "prefix_reset_reason": "",
            "compaction_id": None,
            "latency_ms": 1,
        },
        {
            "request_index": 0,
            "provider": "CacheProvider",
            "model": None,
            "prompt_tokens": 100,
            "completion_tokens": None,
            "cache_read_tokens": 0,
            "cache_write_tokens": None,
            "uncached_tokens": 100,
            "cache_hit_rate": 0.0,
            "system_prefix_estimated_tokens": None,
            "project_prefix_estimated_tokens": None,
            "conversation_estimated_tokens": None,
            "prefix_hash": "",
            "previous_request_is_prefix": False,
            "longest_common_prefix_estimated_tokens": 0,
            "prefix_reset_reason": "",
            "compaction_id": None,
            "latency_ms": 1,
        },
    ]


def test_model_turn_runner_distinguishes_unreported_cache_metrics_from_zero_hits() -> None:
    unsupported_state = RunState.start("unsupported")
    ModelTurnRunner(CacheProvider([ModelUsage(prompt_tokens=100)])).next_turn(unsupported_state, [])

    zero_state = RunState.start("zero")
    ModelTurnRunner(
        CacheProvider(
            [ModelUsage(prompt_tokens=100, cached_tokens=0, cache_hit_tokens=0, cache_miss_tokens=100)]
        )
    ).next_turn(zero_state, [])

    assert unsupported_state.metrics["cache_metrics_available"] is False
    assert unsupported_state.metrics["cache_hit_rate"] is None
    assert unsupported_state.metrics["cache_unreported_requests"] == 1
    assert zero_state.metrics["cache_metrics_available"] is True
    assert zero_state.metrics["cache_hit_rate"] == 0.0
