from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.action_handler import ActionHandler
from minicc.core.prompt import PromptBuilder
from minicc.core.protocol import BashAction
from minicc.core.provider import CompletionOptions, ModelProvider
from minicc.core.runner import ModelTurnConfig, ModelTurnRunner
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.policy.base import PolicyChain


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
        prompt_builder: PromptBuilder | None = None,
        policy_chain: PolicyChain | None = None,
        session: SessionManager | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self.config = config or LoopConfig()
        self.session = session or SessionManager()
        self.turn_runner = ModelTurnRunner(
            provider,
            prompt_builder=prompt_builder,
            config=ModelTurnConfig(
                max_protocol_errors=self.config.max_protocol_errors,
                max_action_timeout_sec=self.config.max_action_timeout_sec,
                model_options=self.config.model_options,
            ),
        )
        self.action_handler = ActionHandler(
            executor,
            policy_chain=policy_chain,
            session=self.session,
        )

    def run(self, state: RunState) -> AgentLoopResult:
        trajectory: list[TrajectoryStep] = []

        while state.status == "running":
            if state.metrics["turns"] >= self.config.max_turns:
                self.session.fail(state, "Run failed because max_turns was exhausted.")
                break

            turn = self.turn_runner.next_turn(state, trajectory)
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
