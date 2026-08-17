from __future__ import annotations

from dataclasses import dataclass

from minicc.core.loop import AgentLoop
from minicc.core.protocol import BashAction, FinalAction
from minicc.core.provider import ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState
from minicc.core.verification import CommandCompletionVerifier, VerificationResult


@dataclass
class FinalProvider:
    responses: list[str]

    def complete(self, messages, *, options=None):
        return ModelResponse(
            text=self.responses.pop(0),
            raw={},
            usage=ModelUsage(prompt_tokens=5, completion_tokens=2),
            latency_ms=1,
        )


class SequenceExecutor:
    def __init__(self, observations: list[Observation]) -> None:
        self.observations = observations
        self.commands: list[str] = []

    def run(self, action: BashAction, state: RunState) -> Observation:
        self.commands.append(action.command)
        return self.observations.pop(0)


def test_loop_rejects_final_and_returns_verification_failure_to_model(tmp_path) -> None:
    class RejectOnceVerifier:
        def __init__(self) -> None:
            self.calls = 0

        def verify(self, state, execute):
            del state, execute
            self.calls += 1
            if self.calls == 1:
                return VerificationResult(
                    False,
                    observation=Observation(
                        kind="verification_error",
                        exit_code=1,
                        stderr_preview="one test failed",
                        message="The authoritative test command failed.",
                    ),
                )
            return VerificationResult(True, reason="verified")

    verifier = RejectOnceVerifier()
    provider = FinalProvider(
        [
            '{"type":"final","answer":"done too early"}',
            '{"type":"final","answer":"fixed and verified"}',
        ]
    )
    state = RunState.start("Fix the task")

    result = AgentLoop(
        provider,
        SequenceExecutor([]),
        completion_verifier=verifier,
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.state.final_answer == "fixed and verified"
    assert result.state.metrics["model_final_requests"] == 2
    assert result.state.metrics["verification_attempts"] == 2
    assert result.state.metrics["verification_rejected"] == 1
    assert result.state.metrics["verification_passed"] == 1
    assert len(result.trajectory) == 1
    assert isinstance(result.trajectory[0].action, FinalAction)
    assert result.trajectory[0].observation.kind == "verification_error"
    assert "one test failed" in result.trajectory[0].observation.stderr_preview


def test_command_verifier_runs_through_handler_execution_boundary(tmp_path) -> None:
    executor = SequenceExecutor(
        [
            Observation(kind="command_error", exit_code=1, stderr_preview="failed"),
            Observation(kind="command_result", exit_code=0, stdout_preview="passed"),
        ]
    )
    provider = FinalProvider(
        [
            '{"type":"final","answer":"first"}',
            '{"type":"final","answer":"second"}',
        ]
    )
    state = RunState.start("Verify before completion")

    result = AgentLoop(
        provider,
        executor,
        completion_verifier=CommandCompletionVerifier(("python -m unittest",)),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert executor.commands == ["python -m unittest", "python -m unittest"]
    assert result.state.metrics["bash_actions"] == 0
    assert result.state.metrics["verification_bash_actions"] == 2
    assert result.state.metrics["verification_command_failures"] == 1
    assert result.trajectory[0].observation.kind == "verification_error"
