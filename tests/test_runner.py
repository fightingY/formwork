import json
from dataclasses import dataclass

from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage, NativeToolCall
from minicc.core.runner import ModelTurnRunner
from minicc.core.state import RunState
from minicc.trace.recorder import TraceRecorder


def _call(name: str, arguments: dict | None = None, *, call_id: str = "c1") -> NativeToolCall:
    return NativeToolCall(id=call_id, name=name, arguments=json.dumps(arguments or {}))


@dataclass
class FakeProvider:
    responses: list[tuple[NativeToolCall, ...]]

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            text="",
            raw={},
            usage=ModelUsage(prompt_tokens=3, completion_tokens=2),
            latency_ms=5,
            tool_calls=self.responses.pop(0),
        )


def test_model_turn_runner_parses_action_and_records_usage() -> None:
    state = RunState.start("finish")
    runner = ModelTurnRunner(FakeProvider([(_call("final", {"answer": "done"}),)]))

    turn = runner.next_turn(state, [{"role": "user", "content": "finish"}])

    assert len(turn.actions) == 1
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
                text="",
                raw={"model": "actual-model", "system_fingerprint": "backend-1"},
                usage=ModelUsage(prompt_tokens=3),
                latency_ms=5,
                tool_calls=(_call("final", {"answer": "done"}),),
            )

    state = RunState.start("finish")
    ModelTurnRunner(IdentityProvider()).next_turn(state, [])

    assert state.metrics["provider_response_models"] == ["actual-model"]
    assert state.metrics["provider_system_fingerprints"] == ["backend-1"]


def test_model_turn_runner_passes_attempt_metadata_through_to_trace() -> None:
    state = RunState.start("finish")
    trace = TraceRecorder()
    ModelTurnRunner(
        FakeProvider([(_call("final", {"answer": "done"}),)]),
        trace=trace,
    ).next_turn(state, [], attempt_count=2, retry_reasons=("timeout",))

    assert trace.events[0]["attempt_count"] == 2
    assert trace.events[0]["retry_reasons"] == ["timeout"]


def test_model_turn_runner_defaults_attempt_metadata_to_single() -> None:
    state = RunState.start("finish")
    trace = TraceRecorder()
    ModelTurnRunner(FakeProvider([(_call("final", {"answer": "done"}),)]), trace=trace).next_turn(
        state, []
    )

    assert trace.events[0]["attempt_count"] == 1
    assert trace.events[0]["retry_reasons"] == []


def test_model_turn_runner_fails_the_turn_when_provider_returns_no_tool_calls() -> None:
    # tool_choice="required" makes an empty tool_calls array a provider-contract
    # violation, not a recoverable "bad model output" case — there is no more
    # per-runner protocol-error retry budget (the old text-JSON protocol's
    # ``max_protocol_errors`` concept no longer exists). The runner fails the
    # run outright on the very first such response.
    class EmptyToolCallsProvider:
        def complete(self, messages, *, options=None):
            return ModelResponse(
                text="",
                raw={},
                usage=ModelUsage(),
                latency_ms=1,
                tool_calls=(),
            )

    state = RunState.start("bad")
    runner = ModelTurnRunner(EmptyToolCallsProvider())

    turn = runner.next_turn(state, [{"role": "user", "content": "bad"}])

    assert turn.should_continue is False
    assert turn.actions == ()
    assert turn.observation is not None
    assert turn.observation.kind == "command_error"
    assert state.status == "failed"


def test_model_turn_runner_returns_non_terminal_observation_on_bad_tool_call_arguments() -> None:
    # A single tool_call whose arguments string fails to decode/validate is a
    # runtime-recoverable per-call error (protocol_error), distinct from the
    # provider-contract violation above: the run is not failed, just fed a
    # correction observation.
    state = RunState.start("bad args")
    runner = ModelTurnRunner(FakeProvider([(NativeToolCall(id="bad", name="bash", arguments="not json"),)]))

    turn = runner.next_turn(state, [{"role": "user", "content": "bad args"}])

    assert turn.should_continue is True
    assert turn.actions == ()
    assert turn.observation is not None
    assert turn.observation.kind == "protocol_error"
    assert state.status == "running"


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
            text="",
            raw={},
            usage=self.usages.pop(0),
            latency_ms=1,
            tool_calls=(_call("bash", {"command": "true"}),),
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
