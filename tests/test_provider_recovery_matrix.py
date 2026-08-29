from pathlib import Path

import pytest

from minicc.core.events import EventLog
from minicc.core.projections import ExecutionProjection, ProjectionRegistry
from minicc.core.provider import (
    ABORTED,
    CompletionOptions,
    LlmFailure,
    ModelResponse,
    ModelUsage,
    ProviderError,
    RetryPolicy,
)
from minicc.core.retry import run_with_retry
from minicc.core.runner import ModelTurnConfig, ModelTurnRunner


class FlakyRunner:
    def __init__(self):
        self.calls = 0

    def next_turn(self, state, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError(failure=LlmFailure(message="rate limited", code="rate_limit"))
        return type("Turn", (), {"actions": (), "observation": None, "should_continue": False})()


def test_retry_is_durable_and_bounded(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    runner = FlakyRunner()
    state = type("State", (), {"metrics": {}, "_event_log": log})()
    policy = RetryPolicy(max_retries=1, retryable_codes=("rate_limit",))
    turn = run_with_retry(
        runner,
        state=state,
        messages=[],
        route_name="primary",
        provider=object(),
        policy=policy,
        sleep_fn=lambda _: None,
    )
    assert turn.should_continue is False
    assert runner.calls == 2
    assert [e.type for e in log.events] == ["llm/retry"]


def test_unknown_tool_outcome_blocks_automatic_retry(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    log.append("tool/call", {"call_id": "side-effect", "name": "bash", "started": True})
    registry = ProjectionRegistry()
    registry.register(ExecutionProjection())
    registry.fold("s", log.events)
    execution = registry.value("s", "execution")
    assert execution["unknown_tool_outcomes"]
    assert execution["allow_automatic_retry"] is False


def test_retry_exhaustion_does_not_loop_forever(tmp_path: Path) -> None:
    class AlwaysFail:
        def next_turn(self, state, messages, **kwargs):
            raise ProviderError(failure=LlmFailure(message="down", code="server"))

    state = type(
        "State",
        (),
        {"metrics": {}, "_event_log": EventLog(tmp_path / "events.jsonl", session_id="s")},
    )()
    with pytest.raises(ProviderError):
        run_with_retry(
            AlwaysFail(),
            state=state,
            messages=[],
            route_name="primary",
            provider=object(),
            policy=RetryPolicy(max_retries=2),
            sleep_fn=lambda _: None,
        )


def test_runner_persists_stream_chunks_before_completion(tmp_path: Path) -> None:
    class StreamingProvider:
        provider_name = "mock"
        model = "mock-model"

        def complete(self, messages, *, options=None):
            assert options is not None and options.stream
            options.on_chunk({"type": "text-delta", "text": "hello"})
            options.on_chunk(
                {"type": "usage", "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
            )
            return ModelResponse(
                "hello", {}, ModelUsage(prompt_tokens=3, completion_tokens=1, total_tokens=4), 1
            )

    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    state = type(
        "State",
        (),
        {"metrics": {"turns": 0}, "_event_log": log, "_event_turn": 0, "_event_step": 0},
    )()
    runner = ModelTurnRunner(
        StreamingProvider(),
        config=ModelTurnConfig(model_options=CompletionOptions(stream=True, tool_choice="none")),
    )
    runner.next_turn(state, [])
    assert [e.type for e in log.events] == ["assistant/chunk", "assistant/chunk"]


def test_streaming_provider_receives_runtime_cancellation() -> None:
    from threading import Event

    from minicc.core.provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.base_url = "http://mock"
    provider.api_key = "key"
    provider.extra_headers = {}
    cancel = Event()
    cancel.set()

    class Response:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield "data: {}"

    class Client:
        def stream(self, *args, **kwargs):
            class Context:
                def __enter__(self):
                    return Response()

                def __exit__(self, *args):
                    pass

            return Context()

        def close(self):
            pass

    provider._request_client = lambda: Client()
    provider.stream_idle_timeout_sec = 1
    provider._discard_client = lambda client: None
    with pytest.raises(ProviderError) as error:
        provider._complete_stream({}, cancel_event=cancel)
    assert error.value.failure.code == ABORTED
