from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState, load_run_state, save_run_state, state_path_for_run
from minicc.policy.base import PolicyDecision


class BashExecutorProtocol(Protocol):
    def run(self, action: BashAction, state: RunState) -> Observation:
        ...


@dataclass
class SessionManager:
    runs_root: Path | None = None

    def save(self, state: RunState) -> Path:
        if state.run_dir is None and self.runs_root is not None:
            return save_run_state(state, self.state_path(state.run_id))
        return save_run_state(state)

    def load(self, run_id: str) -> RunState:
        return load_run_state(self.state_path(run_id))

    def state_path(self, run_id: str) -> Path:
        return state_path_for_run(run_id, runs_root=self.runs_root)

    def fail(self, state: RunState, message: str) -> None:
        state.status = "failed"
        state.state_summary = message

    def request_ask(self, state: RunState, question: str) -> None:
        state.status = "waiting_approval"
        state.open_questions.append(question)
        state.approval_question = question
        self.save(state)

    def request_approval(
        self,
        state: RunState,
        action: BashAction,
        decision: PolicyDecision,
    ) -> None:
        state.status = "waiting_approval"
        state.pending_action = action
        state.approval_question = decision.approval_question or decision.reason
        state.open_questions.append(state.approval_question)
        state.approvals.append(
            {
                "status": "pending",
                "policy_name": decision.policy_name,
                "reason": decision.reason,
                "question": state.approval_question,
                "action": action.command,
            }
        )
        state.metrics["approvals_requested"] = state.metrics.get("approvals_requested", 0) + 1
        self.save(state)

    def approve(self, state: RunState) -> None:
        state.approvals.append(
            {
                "status": "approved",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "action": state.pending_action.command if state.pending_action else None,
            }
        )
        self.save(state)

    def deny(self, state: RunState, reason: str) -> None:
        state.approvals.append(
            {
                "status": "denied",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
                "action": state.pending_action.command if state.pending_action else None,
            }
        )
        self.save(state)

    def apply_pending_approval_result(
        self,
        state: RunState,
        executor: BashExecutorProtocol,
    ) -> None:
        if state.pending_action is None:
            latest = latest_approval_result(state)
            if latest is not None and latest.get("status") == "denied":
                reason = str(latest.get("reason") or "User replied to the pending question.")
                state.last_observation = Observation(
                    kind="approval_result",
                    message=f"User response for pending question: {reason}",
                )
            state.status = "running"
            state.approval_question = None
            return

        latest = latest_approval_result(state)
        if latest is None:
            return

        if latest.get("status") == "approved":
            action = state.pending_action
            state.pending_action = None
            state.approval_question = None
            state.status = "running"
            observation = executor.run(action, state)
            record_execution_metrics(state, observation)
            state.last_observation = observation
            state.state_summary = "Approved pending action was executed before resuming the agent loop."
            return

        if latest.get("status") == "denied":
            reason = str(latest.get("reason") or "User denied the action.")
            action_text = state.pending_action.command
            state.pending_action = None
            state.approval_question = None
            state.status = "running"
            state.last_observation = Observation(
                kind="approval_result",
                message=f"User denied the pending action. Reason: {reason}",
                stderr_preview=action_text,
            )
            state.state_summary = (
                "The previous pending action was denied by the user. "
                "Choose a safer alternative or ask a clarifying question."
            )


def latest_approval_result(state: RunState) -> dict | None:
    for approval in reversed(state.approvals):
        if approval.get("status") in {"approved", "denied"}:
            return approval
    return None


def record_execution_metrics(state: RunState, observation: Observation) -> None:
    state.metrics["bash_actions"] = state.metrics.get("bash_actions", 0) + 1
    if observation.kind == "command_error":
        state.metrics["command_failures"] = state.metrics.get("command_failures", 0) + 1
    elif observation.kind == "timeout":
        state.metrics["timeouts"] = state.metrics.get("timeouts", 0) + 1
