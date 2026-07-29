from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.context import state_snapshot_text
from minicc.core.protocol import Action, AskAction, BashAction, FinalAction
from minicc.core.session import (
    SessionManager,
    begin_execution,
    complete_execution,
    record_execution_metrics,
)
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.policy.base import PolicyChain, PolicyDecision
from minicc.trace.recorder import TraceRecorder


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
        trace: TraceRecorder | None = None,
    ) -> None:
        self.executor = executor
        self.policy_chain = policy_chain or PolicyChain()
        self.session = session or SessionManager()
        self.trace = trace

    def handle(self, action: Action, state: RunState) -> ActionOutcome:
        if self.trace is not None:
            self.trace.action_started(state, action)

        if isinstance(action, FinalAction):
            state.status = "completed"
            state.final_answer = action.answer
            return ActionOutcome(should_continue=False)

        if isinstance(action, AskAction):
            self.session.request_ask(state, action.question)
            if self.trace is not None:
                self.trace.approval_requested(state, action.question)
            return ActionOutcome(should_continue=False)

        return self._handle_bash(action, state)

    def _handle_bash(self, action: BashAction, state: RunState) -> ActionOutcome:
        decision = self.policy_chain.evaluate(action, state)
        if self.trace is not None:
            self.trace.policy_decision(state, decision)
        action_to_execute = action

        if decision.type == "deny":
            state.metrics["policy_denials"] = state.metrics.get("policy_denials", 0) + 1
            observation = _policy_violation_observation(decision)
            state.last_observation = observation
            if self.trace is not None:
                self.trace.observation_created(state, observation)
            return ActionOutcome(
                steps=[
                    TrajectoryStep(
                        action=action,
                        observation=observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                ]
            )

        if decision.type == "require_approval":
            self.session.request_approval(state, action, decision)
            if self.trace is not None:
                self.trace.approval_requested(state, state.approval_question or decision.reason)
            return ActionOutcome(should_continue=False)

        if decision.type == "rewrite" and decision.rewritten_action is not None:
            action_to_execute = decision.rewritten_action

        repeated_io = _record_io_action(state, action_to_execute.command)
        if repeated_io:
            observation = Observation(
                kind="policy_violation",
                message=(
                    "repeated I/O guard: the identical read/search command was already run twice; "
                    "inspect the existing result and make the required patch or verify it instead."
                ),
            )
            state.last_observation = observation
            if self.trace is not None:
                self.trace.observation_created(state, observation)
            return ActionOutcome(
                steps=[
                    TrajectoryStep(
                        action=action_to_execute,
                        observation=observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                ]
            )
        if self.trace is not None:
            self.trace.sandbox_exec_started(state, action_to_execute.command)
        execution_id = begin_execution(state, action_to_execute, self.session)
        observation = self.executor.run(action_to_execute, state)
        complete_execution(state, execution_id, observation, self.session)
        record_execution_metrics(state, observation)
        state.last_observation = observation
        if self.trace is not None:
            self.trace.sandbox_exec_finished(state, observation)
            self.trace.observation_created(state, observation)
        return ActionOutcome(
            steps=[
                TrajectoryStep(
                    action=action_to_execute,
                    observation=observation,
                    state_snapshot=state_snapshot_text(state),
                )
            ]
        )


def _policy_violation_observation(decision: PolicyDecision) -> Observation:
    return Observation(
        kind="policy_violation",
        message=f"{decision.policy_name}: {decision.reason}",
    )


def _record_io_action(state: RunState, command: str) -> bool:
    normalized = " ".join(command.strip().split())
    lowered = normalized.lower()
    if not normalized:
        return False

    category: str | None = None
    if re.search(r"(^|[;&|]\s*)(rg|grep|find|fd)(\.exe)?\b", lowered) or "select-string" in lowered:
        category = "search"
    elif re.search(r"(^|[;&|]\s*)(cat|head|tail|type|get-content)(\.exe)?\b", lowered):
        category = "file_read"
    elif re.search(r"(^|[;&|]\s*)sed\s+-n\b", lowered):
        category = "file_read"
    if category is None:
        return False

    metric_name = "search_actions" if category == "search" else "file_read_actions"
    repeated_name = "repeated_searches" if category == "search" else "repeated_file_reads"
    signatures = state.metrics.setdefault("io_action_signatures", {})
    if not isinstance(signatures, dict):
        signatures = {}
        state.metrics["io_action_signatures"] = signatures
    key = f"{category}:{normalized}"
    previous = int(signatures.get(key, 0))
    signatures[key] = previous + 1
    state.metrics[metric_name] = int(state.metrics.get(metric_name, 0)) + 1
    if previous:
        state.metrics[repeated_name] = int(state.metrics.get(repeated_name, 0)) + 1
    return previous >= 2
