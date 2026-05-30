from dataclasses import dataclass

from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig
from minicc.core.protocol import BashAction
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.state import Observation, RunState


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
            usage=ModelUsage(prompt_tokens=10, completion_tokens=2, cached_tokens=5),
            latency_ms=7,
        )


class FakeExecutor(BashExecutor):
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, action: BashAction, state: RunState) -> Observation:
        self.commands.append(action.command)
        return Observation(
            kind="command_result",
            exit_code=0,
            stdout_preview="ok",
            message="Command exited successfully.",
        )


def test_loop_runs_bash_then_final() -> None:
    provider = FakeProvider(
        [
            '{"type":"bash","command":"pytest -q","purpose":"run tests"}',
            '{"type":"final","answer":"Tests passed."}',
        ]
    )
    executor = FakeExecutor()
    state = RunState.start("Run tests")

    result = AgentLoop(provider, executor).run(state)

    assert result.state.status == "completed"
    assert result.state.final_answer == "Tests passed."
    assert executor.commands == ["pytest -q"]
    assert result.state.metrics["turns"] == 2
    assert result.state.metrics["bash_actions"] == 1
    assert result.state.metrics["prompt_tokens"] == 20
    assert result.state.metrics["cached_tokens"] == 10


def test_loop_turns_protocol_error_into_observation_then_recovers() -> None:
    provider = FakeProvider(["not json", '{"type":"final","answer":"Recovered."}'])
    state = RunState.start("Handle protocol error")

    result = AgentLoop(provider, FakeExecutor()).run(state)

    assert result.state.status == "completed"
    assert result.state.metrics["protocol_errors"] == 1
    assert result.trajectory[0].observation.kind == "protocol_error"


def test_loop_waits_on_ask_action() -> None:
    provider = FakeProvider(['{"type":"ask","question":"Which test command should I use?"}'])
    state = RunState.start("Need clarification")

    result = AgentLoop(provider, FakeExecutor()).run(state)

    assert result.state.status == "waiting_approval"
    assert result.state.open_questions == ["Which test command should I use?"]


def test_loop_fails_after_protocol_error_threshold() -> None:
    provider = FakeProvider(["bad 1", "bad 2", "bad 3"])
    state = RunState.start("Bad model")

    result = AgentLoop(
        provider,
        FakeExecutor(),
        config=LoopConfig(max_turns=5, max_protocol_errors=2),
    ).run(state)

    assert result.state.status == "failed"
    assert result.state.metrics["protocol_errors"] == 3
