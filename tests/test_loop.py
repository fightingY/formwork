import json
from dataclasses import dataclass
from pathlib import Path

from minicc.core.context import ContextBuilder, ContextConfig
from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig
from minicc.core.protocol import BashAction
from minicc.core.provider import (
    CONTEXT_OVERFLOW,
    TIMEOUT,
    CompletionOptions,
    LlmFailure,
    ModelResponse,
    ModelUsage,
    NativeToolCall,
    ProviderError,
)
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState
from minicc.core.tooling import HybridToolRunner, ToolCallScheduler
from minicc.policy.base import PolicyChain, PolicyDecision
from minicc.policy.network import NetworkPolicy
from minicc.trace.recorder import TraceRecorder


def _call(name: str, arguments: dict | None = None, *, call_id: str = "c1") -> NativeToolCall:
    return NativeToolCall(id=call_id, name=name, arguments=json.dumps(arguments or {}))


def _scheduler(executor) -> ToolCallScheduler:
    # bash is dispatched as a ``ToolCall`` through ToolCallScheduler/HybridToolRunner
    # under native tool calling, not directly by ActionHandler like the old
    # text-JSON BashAction path — tests exercising bash need this wired up.
    return ToolCallScheduler(HybridToolRunner(executor), max_parallel_tool_calls=1)


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
            usage=ModelUsage(prompt_tokens=10, completion_tokens=2, cached_tokens=5),
            latency_ms=7,
            tool_calls=self.responses.pop(0),
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
            (_call("bash", {"command": "pytest -q", "description": "run tests"}),),
            (_call("final", {"answer": "Tests passed."}),),
        ]
    )
    executor = FakeExecutor()
    state = RunState.start("Run tests")

    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.state.final_answer == "Tests passed."
    assert executor.commands == ["pytest -q"]
    assert result.state.metrics["turns"] == 2
    assert result.state.metrics["bash_actions"] == 1
    assert result.state.metrics["prompt_tokens"] == 20
    assert result.state.metrics["cached_tokens"] == 10


def test_loop_turns_protocol_error_into_observation_then_recovers(tmp_path) -> None:
    # A tool_call whose ``arguments`` string fails to JSON-decode is the current
    # equivalent of the old "malformed text" case: the provider API still
    # guarantees a structurally valid tool_calls array, but the JSON text inside
    # ``function.arguments`` is still adapter-validated per call.
    bad_call = NativeToolCall(id="bad", name="bash", arguments="not json")
    provider = FakeProvider([(bad_call,), (_call("final", {"answer": "Recovered."}),)])
    state = RunState.start("Handle protocol error")

    executor = FakeExecutor()
    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.trajectory[0].observation.kind == "protocol_error"


def test_loop_waits_on_ask_action(tmp_path) -> None:
    provider = FakeProvider([(_call("ask", {"question": "Which test command should I use?"}),)])
    state = RunState.start("Need clarification")

    result = AgentLoop(provider, FakeExecutor(), session=SessionManager(runs_root=tmp_path / "runs")).run(state)

    assert result.state.status == "waiting_approval"
    assert result.state.open_questions == ["Which test command should I use?"]


def test_loop_recovers_from_repeated_protocol_errors_without_a_cap(tmp_path) -> None:
    # LoopConfig no longer has ``max_protocol_errors`` (the old text-JSON retry
    # budget was deleted along with the text-JSON protocol): a per-call argument
    # ProtocolError is always non-terminal now, however many times it recurs.
    provider = FakeProvider(
        [
            (NativeToolCall(id="bad1", name="bash", arguments="{}"),),
            (NativeToolCall(id="bad2", name="bash", arguments="{}"),),
            (NativeToolCall(id="bad3", name="bash", arguments="{}"),),
            (_call("final", {"answer": "done"}),),
        ]
    )
    state = RunState.start("Bad model")

    result = AgentLoop(
        provider,
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.state.final_answer == "done"
    protocol_error_steps = [
        step for step in result.trajectory if step.observation.kind == "protocol_error"
    ]
    assert len(protocol_error_steps) == 3


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
            (_call("bash", {"command": "sudo apt update"}),),
            (_call("final", {"answer": "Stopped."}),),
        ]
    )

    class DenyPolicy:
        name = "DenyPolicy"

        def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
            return PolicyDecision(type="deny", reason="nope", policy_name=self.name)

    executor = FakeExecutor()
    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
        policy_chain=PolicyChain([DenyPolicy()]),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(RunState.start("deny command"))

    # bash now dispatches through ToolCallScheduler, whose ordered-results step
    # aggregates into a generic command_error/command_result Observation kind;
    # the individual tool result's ``content["kind"]`` still carries the
    # original policy_violation classification.
    assert result.state.status == "completed"
    assert result.trajectory[0].observation.kind == "command_error"
    payload = json.loads(result.trajectory[0].observation.stdout_preview)
    assert payload[0]["content"]["kind"] == "policy_violation"
    assert result.state.metrics["policy_denials"] == 1


def test_loop_waits_for_policy_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([(_call("bash", {"command": "pip install pytest"}),)])
    state = RunState.start("install dependency", run_dir=tmp_path)
    executor = FakeExecutor()

    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
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
            (_call("bash", {"command": "pytest -q", "description": "run tests"}),),
            (_call("final", {"answer": "Tests passed."}),),
        ]
    )
    state = RunState.start("Run tests", run_dir=tmp_path)
    trace_path = tmp_path / "trace.jsonl"
    executor = FakeExecutor()

    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
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
            (_call("bash", {"command": "a"}, call_id="a"),),
            (_call("bash", {"command": "b"}, call_id="b"),),
            (_call("bash", {"command": "c"}, call_id="c"),),
            (_call("final", {"answer": "done"}),),
        ]
    )
    state = RunState.start("many turns")
    executor = FakeExecutor()

    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.state.metrics["turns"] == 4


def test_loop_fails_when_max_turns_exhausted(tmp_path) -> None:
    provider = FakeProvider(
        [
            (_call("bash", {"command": "a"}, call_id="a"),),
            (_call("bash", {"command": "b"}, call_id="b"),),
            (_call("bash", {"command": "c"}, call_id="c"),),
            (_call("final", {"answer": "done"}),),
        ]
    )
    state = RunState.start("capped turns")
    executor = FakeExecutor()

    result = AgentLoop(
        provider,
        executor,
        tool_scheduler=_scheduler(executor),
        session=SessionManager(runs_root=tmp_path / "runs"),
        config=LoopConfig(max_turns=2),
    ).run(state)

    assert result.state.status == "failed"
    assert "max_turns" in result.state.state_summary
    assert result.state.metrics["turns"] == 2


def test_loop_fails_fast_when_provider_returns_no_tool_calls(tmp_path) -> None:
    # tool_choice="required" guarantees a structurally valid tool_calls array from
    # a well-behaved provider; a response that still comes back empty (e.g. the
    # model got cut off at a token limit before emitting one) is a provider-contract
    # violation the loop can't recover from, so it fails the run outright on the
    # first turn rather than looping.
    class LengthTruncatedProvider:
        def complete(self, messages, *, options=None):
            return ModelResponse(
                text='{"type":"final","answer":"unterminated',
                raw={"choices": [{"finish_reason": "length"}]},
                usage=ModelUsage(prompt_tokens=10, completion_tokens=2048),
                latency_ms=7,
                finish_reason="length",
                tool_calls=(),
            )

    state = RunState.start("Truncated answer")

    result = AgentLoop(
        LengthTruncatedProvider(),
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "failed"
    assert "no tool_calls" in result.state.state_summary
    assert result.state.metrics.get("protocol_errors", 0) == 0
    assert result.state.metrics["turns"] == 1


class _OverflowThenOkProvider:
    """先跑 2 个 bash 回合撑起轨迹，第 3 次调用抛 CONTEXT_OVERFLOW，之后回复 final。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *, options=None):
        self.calls += 1
        if self.calls == 3:
            raise ProviderError(
                failure=LlmFailure(message="context too long", code=CONTEXT_OVERFLOW)
            )
        if self.calls <= 2:
            return ModelResponse(
                text="",
                raw={},
                usage=ModelUsage(),
                latency_ms=1,
                tool_calls=(_call("bash", {"command": "echo step", "description": "grow"}),),
            )
        return ModelResponse(
            text="",
            raw={},
            usage=ModelUsage(),
            latency_ms=1,
            tool_calls=(_call("final", {"answer": "done"}),),
        )


def test_overflow_recovery_compacts_and_retries(tmp_path) -> None:
    builder = ContextBuilder(ContextConfig(recent_turns=0))
    state = RunState.start("grow then overflow")
    executor = FakeExecutor()

    result = AgentLoop(
        _OverflowThenOkProvider(),
        executor,
        tool_scheduler=_scheduler(executor),
        context_builder=builder,
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "completed"
    assert result.state.metrics["context_overflow_retries"] == 1
    assert result.state.metrics["context_compactions"] == 1


def test_overflow_recovery_exhausts_retries_and_fails(tmp_path) -> None:
    class AlwaysOverflow:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, *, options=None):
            self.calls += 1
            if self.calls <= 2:
                return ModelResponse(
                    text="",
                    raw={},
                    usage=ModelUsage(),
                    latency_ms=1,
                    tool_calls=(_call("bash", {"command": "echo step", "description": "grow"}),),
                )
            raise ProviderError(
                failure=LlmFailure(message="context too long", code=CONTEXT_OVERFLOW)
            )

    builder = ContextBuilder(ContextConfig(recent_turns=0))
    state = RunState.start("persistent overflow")
    executor = FakeExecutor()

    result = AgentLoop(
        AlwaysOverflow(),
        executor,
        tool_scheduler=_scheduler(executor),
        context_builder=builder,
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "failed"
    assert result.state.metrics["provider_errors"] == 2
    assert result.state.metrics["context_overflow_retries"] == 1


def test_overflow_recovery_disabled_when_zero(tmp_path) -> None:
    builder = ContextBuilder(ContextConfig(recent_turns=0, max_overflow_retries=0))
    state = RunState.start("no recovery")
    executor = FakeExecutor()

    result = AgentLoop(
        _OverflowThenOkProvider(),
        executor,
        tool_scheduler=_scheduler(executor),
        context_builder=builder,
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "failed"
    assert "context_overflow_retries" not in result.state.metrics


def test_overflow_recovery_with_empty_trajectory_fails_without_looping(tmp_path) -> None:
    class ImmediateOverflow:
        def complete(self, messages, *, options=None):
            raise ProviderError(
                failure=LlmFailure(message="context too long", code=CONTEXT_OVERFLOW)
            )

    state = RunState.start("overflow on first turn")

    result = AgentLoop(
        ImmediateOverflow(),
        FakeExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "failed"
    assert result.state.metrics["provider_errors"] == 1


def test_overflow_recovery_disabled_compaction_fails(tmp_path) -> None:
    builder = ContextBuilder(
        ContextConfig(recent_turns=0, compaction_strategy="disabled")
    )
    state = RunState.start("compaction disabled")
    executor = FakeExecutor()

    result = AgentLoop(
        _OverflowThenOkProvider(),
        executor,
        tool_scheduler=_scheduler(executor),
        context_builder=builder,
        session=SessionManager(runs_root=tmp_path / "runs"),
    ).run(state)

    assert result.state.status == "failed"
    assert result.state.metrics.get("context_compactions", 0) == 0
