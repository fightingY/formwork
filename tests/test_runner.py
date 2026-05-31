from dataclasses import dataclass

from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.runner import ModelTurnConfig, ModelTurnRunner
from minicc.core.state import RunState


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
