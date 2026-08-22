import json
from dataclasses import dataclass
from pathlib import Path

from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig
from minicc.core.protocol import BashAction
from minicc.core.provider import (
    TIMEOUT,
    CompletionOptions,
    LlmFailure,
    ModelResponse,
    ModelUsage,
    ProviderError,
)
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState
from minicc.policy.base import PolicyChain, PolicyDecision
from minicc.policy.network import NetworkPolicy
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


def test_loop_runs_bash_then_final(tmp_path) -> None:
    provider = FakeProvider(
        [
            '{"type":"bash","command":"pytest -q","purpose":"run tests"}',
            '{"type":"final","answer":"Tests passed."}',
        ]
    )
    executor = FakeExecutor()
    state = RunState.start("Run tests")

    result = AgentLoop(provider, executor, session=SessionManager(runs_root=tmp_path / "runs")).run(state)

    assert result.state.status == "completed"
    assert result.state.final_answer == "Tests passed."
    assert executor.commands == ["pytest -q"]
    assert result.state.metrics["turns"] == 2
    assert result.state.metrics["bash_actions"] == 1
    assert result.state.metrics["prompt_tokens"] == 20
    assert result.state.metrics["cached_tokens"] == 10


def test_loop_turns_protocol_error_into_observation_then_recovers(tmp_path) -> None:
    provider = FakeProvider(["not json", '{"type":"final","answer":"Recovered."}'])
    state = RunState.start("Handle protocol error")

    result = AgentLoop(provider, FakeExecutor(), session=SessionManager(runs_root=tmp_path / "runs")).run(state)

    assert result.state.status == "completed"
    assert result.state.metrics["protocol_errors"] == 1
    assert result.trajectory[0].observation.kind == "protocol_error"


def test_loop_waits_on_ask_action(tmp_path) -> None:
    provider = FakeProvider(['{"type":"ask","question":"Which test command should I use?"}'])
    state = RunState.start("Need clarification")

    result = AgentLoop(provider, FakeExecutor(), session=SessionManager(runs_root=tmp_path / "runs")).run(state)

    assert result.state.status == "waiting_approval"
    assert result.state.open_questions == ["Which test command should I use?"]


def test_loop_fails_after_protocol_error_threshold(tmp_path) -> None:
    provider = FakeProvider(["bad 1", "bad 2", "bad 3"])
    state = RunState.start("Bad model")

    result = AgentLoop(
        provider,
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
        config=LoopConfig(max_protocol_errors=2),
    ).run(state)

    assert result.state.status == "failed"
    assert result.state.metrics["protocol_errors"] == 3
    saved = json.loads((tmp_path / "runs" / state.run_id / "state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed"


def test_loop_persists_provider_failure_instead_of_raising(tmp_path) -> None:
    class FailingProvider:
        def complete(self, messages, *, options=None):
            raise ProviderError(
                failure=LlmFailure(
                    message="Provider HTTP request failed: ReadTimeout",
                    code=TIMEOUT,
                )
            )

    state = RunState.start("Handle provider timeout")
    trace_path = tmp_path / "runs" / state.run_id / "trace.jsonl"

    result = AgentLoop(
        FailingProvider(),
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
        trace=TraceRecorder(trace_path),
    ).run(state)

    assert result.state.status == "failed"
    assert result.state.metrics["provider_errors"] == 1
    saved = json.loads((tmp_path / "runs" / state.run_id / "state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["metrics"]["completed_at"] is not None
    events = [json.loads(line)["event"] for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert events[-2:] == ["provider_error", "run_failed"]


def test_loop_turns_policy_deny_into_observation(tmp_path) -> None:
    provider = FakeProvider(
        [
            '{"type":"bash","command":"sudo apt update"}',
            '{"type":"final","answer":"Stopped."}',
        ]
    )

    class DenyPolicy:
        name = "DenyPolicy"

        def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
            return PolicyDecision(type="deny", reason="nope", policy_name=self.name)

    result = AgentLoop(
        provider,
        FakeExecutor(),
        policy_chain=PolicyChain([DenyPolicy()]),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(RunState.start("deny command"))

    assert result.state.status == "completed"
    assert result.trajectory[0].observation.kind == "policy_violation"
    assert result.state.metrics["policy_denials"] == 1


def test_loop_waits_for_policy_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider(['{"type":"bash","command":"pip install pytest"}'])
    state = RunState.start("install dependency", run_dir=tmp_path)

    result = AgentLoop(
        provider,
        FakeExecutor(),
        policy_chain=PolicyChain([NetworkPolicy(mode="locked", require_approval=True)]),
    ).run(state)

    assert result.state.status == "waiting_approval"
    assert result.state.pending_action == BashAction(command="pip install pytest")
    assert result.state.approval_question is not None
    assert result.state.metrics["approvals_requested"] == 1
    assert (tmp_path / "state.json").exists()


def test_loop_records_trace_and_metrics(tmp_path) -> None:
    provider = FakeProvider(
        [
            '{"type":"bash","command":"pytest -q","purpose":"run tests"}',
            '{"type":"final","answer":"Tests passed."}',
        ]
    )
    state = RunState.start("Run tests", run_dir=tmp_path)
    trace_path = tmp_path / "trace.jsonl"

    result = AgentLoop(
        provider,
        FakeExecutor(),
        trace=TraceRecorder(trace_path),
    ).run(state)

    events = [json.loads(line)["event"] for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert result.state.status == "completed"
    assert "run_started" in events
    assert "prompt_built" in events
    assert "model_response" in events
    assert "policy_decision" in events
    assert "sandbox_exec_started" in events
    assert "sandbox_exec_finished" in events
    assert "run_completed" in events
    assert (tmp_path / "metrics.json").exists()
    report = json.loads((tmp_path / "run_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["passed"] is True
    assert Path(report["evidence"]["diff"]).parts[-2:] == ("artifacts", "diff.patch")
    assert (tmp_path / "run_report.md").exists()


def test_loop_default_config_has_no_turn_cap(tmp_path) -> None:
    provider = FakeProvider(
        [
            '{"type":"bash","command":"a"}',
            '{"type":"bash","command":"b"}',
            '{"type":"bash","command":"c"}',
            '{"type":"final","answer":"done"}',
        ]
    )
    state = RunState.start("many turns")

    result = AgentLoop(
        provider,
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.state.metrics["turns"] == 4


def test_loop_fails_when_max_turns_exhausted(tmp_path) -> None:
    provider = FakeProvider(
        [
            '{"type":"bash","command":"a"}',
            '{"type":"bash","command":"b"}',
            '{"type":"bash","command":"c"}',
            '{"type":"final","answer":"done"}',
        ]
    )
    state = RunState.start("capped turns")

    result = AgentLoop(
        provider,
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
        config=LoopConfig(max_turns=2),
    ).run(state)

    assert result.state.status == "failed"
    assert "max_turns" in result.state.state_summary
    assert result.state.metrics["turns"] == 2


def test_loop_fails_fast_when_provider_truncates_at_token_limit(tmp_path) -> None:
    class LengthTruncatedProvider:
        def complete(self, messages, *, options=None):
            return ModelResponse(
                text='{"type":"final","answer":"unterminated',
                raw={"choices": [{"finish_reason": "length"}]},
                usage=ModelUsage(prompt_tokens=10, completion_tokens=2048),
                latency_ms=7,
                finish_reason="length",
            )

    state = RunState.start("Truncated answer")

    result = AgentLoop(
        LengthTruncatedProvider(),
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "failed"
    assert "finish_reason=length" in result.state.state_summary
    assert result.state.metrics.get("protocol_errors", 0) == 0
