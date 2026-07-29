from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.action_handler import ActionHandler
from minicc.core.checkpoint import CheckpointManager
from minicc.core.context import ContextBuilder, state_snapshot_text
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
    interrupt_after_steps: int | None = None


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
        checkpoint_manager: CheckpointManager | None = None,
    ) -> None:
        self.config = config or LoopConfig()
        self.session = session or SessionManager()
        self.context_builder = context_builder or ContextBuilder()
        self.trace = trace or TraceRecorder()
        self.context_builder.trace = self.trace
        self.lifecycle = RunLifecycle(self.trace)
        self.checkpoint_manager = checkpoint_manager
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

    def run(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep] | None = None,
    ) -> AgentLoopResult:
        resumed = trajectory is not None
        trajectory = list(trajectory or [])
        state.metrics["max_turns"] = self.config.max_turns
        if resumed:
            self.lifecycle.resume(state, len(trajectory))
        else:
            self.lifecycle.start(state)
        self._checkpoint(state, trajectory, "resume_started" if resumed else "run_started")
        if self._should_interrupt(trajectory):
            self._interrupt(state, trajectory)

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
                trajectory.append(
                    TrajectoryStep(
                        action=turn.action,
                        observation=turn.observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                )
                self._checkpoint(state, trajectory, "model_observation_recorded")

            if not turn.should_continue or state.status != "running":
                break

            if turn.action is None:
                continue

            outcome = self.action_handler.handle(turn.action, state)
            trajectory.extend(outcome.steps)
            self.session.save(state)
            reason = "action_completed"
            if state.status == "waiting_approval":
                reason = "waiting_approval"
            elif state.status == "completed":
                reason = "run_completed"
            self._checkpoint(state, trajectory, reason)

            if state.status == "running" and self._should_interrupt(trajectory):
                self._interrupt(state, trajectory)

            if not outcome.should_continue:
                break

        self.lifecycle.finish(state)
        if state.status == "interrupted":
            self._checkpoint(state, trajectory, "interrupted_finalized")
        if state.run_dir is not None or self.session.runs_root is not None:
            self.session.save(state)
        return AgentLoopResult(state=state, trajectory=trajectory)

    def _checkpoint(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
        reason: str,
    ) -> None:
        if self.checkpoint_manager is not None:
            self.checkpoint_manager.create(state, trajectory, reason=reason)

    def _should_interrupt(self, trajectory: list[TrajectoryStep]) -> bool:
        threshold = self.config.interrupt_after_steps
        return threshold is not None and len(trajectory) >= threshold

    def _interrupt(self, state: RunState, trajectory: list[TrajectoryStep]) -> None:
        state.status = "interrupted"
        state.metrics["interrupted_after_steps"] = len(trajectory)
        self._checkpoint(state, trajectory, "controlled_interrupt")
        self.session.save(state)


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
