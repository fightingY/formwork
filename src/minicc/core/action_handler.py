from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.protocol import Action, AskAction, BashAction, FinalAction
from minicc.core.session import SessionManager, record_execution_metrics
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.policy.base import PolicyChain, PolicyDecision


class BashExecutor(Protocol):
    def run(self, action: BashAction, state: RunState) -> Observation:
        ...


@dataclass
class ActionOutcome:
    steps: list[TrajectoryStep] = field(default_factory=list)
    should_continue: bool = True


class ActionHandler:
    def __init__(
        self,
        executor: BashExecutor,
        *,
        policy_chain: PolicyChain | None = None,
        session: SessionManager | None = None,
    ) -> None:
        self.executor = executor
        self.policy_chain = policy_chain or PolicyChain()
        self.session = session or SessionManager()

    def handle(self, action: Action, state: RunState) -> ActionOutcome:
        if isinstance(action, FinalAction):
            state.status = "completed"
            state.final_answer = action.answer
            return ActionOutcome(should_continue=False)

        if isinstance(action, AskAction):
            self.session.request_ask(state, action.question)
            return ActionOutcome(should_continue=False)

        return self._handle_bash(action, state)

    def _handle_bash(self, action: BashAction, state: RunState) -> ActionOutcome:
        decision = self.policy_chain.evaluate(action, state)
        action_to_execute = action

        if decision.type == "deny":
            state.metrics["policy_denials"] = state.metrics.get("policy_denials", 0) + 1
            observation = _policy_violation_observation(decision)
            state.last_observation = observation
            return ActionOutcome(steps=[TrajectoryStep(action=action, observation=observation)])

        if decision.type == "require_approval":
            self.session.request_approval(state, action, decision)
            return ActionOutcome(should_continue=False)

        if decision.type == "rewrite" and decision.rewritten_action is not None:
            action_to_execute = decision.rewritten_action

        observation = self.executor.run(action_to_execute, state)
        record_execution_metrics(state, observation)
        state.last_observation = observation
        return ActionOutcome(steps=[TrajectoryStep(action=action_to_execute, observation=observation)])


def _policy_violation_observation(decision: PolicyDecision) -> Observation:
    return Observation(
        kind="policy_violation",
        message=f"{decision.policy_name}: {decision.reason}",
    )
