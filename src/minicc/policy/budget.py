from __future__ import annotations

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.policy.base import PolicyDecision


class BudgetPolicy:
    name = "BudgetPolicy"

    def __init__(
        self,
        *,
        max_bash_actions: int = 30,
        max_action_timeout_sec: int = 120,
    ) -> None:
        self.max_bash_actions = max_bash_actions
        self.max_action_timeout_sec = max_action_timeout_sec

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        if state.metrics.get("bash_actions", 0) >= self.max_bash_actions:
            return PolicyDecision(
                type="deny",
                reason=f"Budget exhausted: max_bash_actions={self.max_bash_actions}.",
                policy_name=self.name,
            )

        if action.timeout_sec > self.max_action_timeout_sec:
            rewritten = BashAction(
                command=action.command,
                timeout_sec=self.max_action_timeout_sec,
                purpose=action.purpose,
            )
            return PolicyDecision(
                type="rewrite",
                reason=(
                    f"Reduced action timeout from {action.timeout_sec}s "
                    f"to {self.max_action_timeout_sec}s."
                ),
                rewritten_action=rewritten,
                policy_name=self.name,
            )

        return PolicyDecision(type="allow", reason="Budget allows action.", policy_name=self.name)
