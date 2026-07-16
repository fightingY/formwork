from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.action_handler import ActionHandler
from minicc.core.context import ContextBuilder
from minicc.core.lifecycle import RunLifecycle
from minicc.core.protocol import BashAction
from minicc.core.provider import CompletionOptions, ModelProvider, ProviderError
from minicc.core.runner import ModelTurnConfig, ModelTurnRunner
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.policy.base import PolicyChain
from minicc.trace.recorder import TraceRecorder


class BashExecutor(Protocol):
    def run(self, action: BashAction, state: RunState) -> Observation:
        ...


@dataclass(frozen=True)
class LoopConfig:
    max_turns: int = 8
    max_protocol_errors: int = 2
    max_action_timeout_sec: int = 120
    model_options: CompletionOptions = field(default_factory=CompletionOptions)


@dataclass
class AgentLoopResult:
    state: RunState
    trajectory: list[TrajectoryStep]


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        executor: BashExecutor,
        *,
        context_builder: ContextBuilder | None = None,
        policy_chain: PolicyChain | None = None,
        session: SessionManager | None = None,
        trace: TraceRecorder | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self.config = config or LoopConfig()
        self.session = session or SessionManager()
        self.context_builder = context_builder or ContextBuilder()
        self.trace = trace or TraceRecorder()
        self.context_builder.trace = self.trace
        self.lifecycle = RunLifecycle(self.trace)
        self.turn_runner = ModelTurnRunner(
            provider,
            config=ModelTurnConfig(
                max_protocol_errors=self.config.max_protocol_errors,
                max_action_timeout_sec=self.config.max_action_timeout_sec,
                model_options=self.config.model_options,
            ),
            trace=self.trace,
        )
        self.action_handler = ActionHandler(
            executor,
            policy_chain=policy_chain,
            session=self.session,
            trace=self.trace,
        )

    def run(self, state: RunState) -> AgentLoopResult:
        trajectory: list[TrajectoryStep] = []
        state.metrics["max_turns"] = self.config.max_turns
        self.lifecycle.start(state)

        while state.status == "running":
            if state.metrics["turns"] >= self.config.max_turns:
                self.session.fail(state, "Run failed because max_turns was exhausted.")
                break

            self.context_builder.maybe_compact(state, trajectory)
            messages = self.context_builder.build_messages(state, trajectory)
            try:
                turn = self.turn_runner.next_turn(state, messages)
            except ProviderError as exc:
                state.metrics["provider_errors"] = state.metrics.get("provider_errors", 0) + 1
                self.trace.record("provider_error", state, error=str(exc))
                self.session.fail(state, f"Run failed because the model provider failed: {exc}")
                break
            if turn.observation is not None:
                trajectory.append(TrajectoryStep(action=turn.action, observation=turn.observation))

            if not turn.should_continue or state.status != "running":
                break

            if turn.action is None:
                continue

            outcome = self.action_handler.handle(turn.action, state)
            trajectory.extend(outcome.steps)
            self.session.save(state)

            if not outcome.should_continue:
                break

        self.lifecycle.finish(state)
        if state.run_dir is not None or self.session.runs_root is not None:
            self.session.save(state)
        return AgentLoopResult(state=state, trajectory=trajectory)


class DisabledExecutor:
    """Executor used by tests or callers that want to block command execution."""

    def run(self, action: BashAction, state: RunState) -> Observation:
        return Observation(
            kind="policy_violation",
            message=(
                "Command execution is disabled for this run. "
                "Use a DockerCommandExecutor or LocalCommandExecutor to execute bash actions."
            ),
            stderr_preview=action.command,
        )
