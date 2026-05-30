from __future__ import annotations

import re

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.policy.base import PolicyDecision


class ApprovalPolicy:
    name = "ApprovalPolicy"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        if not self.enabled:
            return PolicyDecision(
                type="allow",
                reason="Destructive-command approval policy is disabled.",
                policy_name=self.name,
            )

        lowered = action.command.lower()
        approval_patterns = [
            r"\brm\s+-r\b",
            r"\brm\s+-f\b",
            r"\bgit\s+clean\b",
            r"\bfind\b.+\s-delete\b",
        ]
        if any(re.search(pattern, lowered) for pattern in approval_patterns):
            return PolicyDecision(
                type="require_approval",
                reason="Command may delete files and requires human approval.",
                approval_question=f"Approve this potentially destructive command? {action.command}",
                policy_name=self.name,
            )
        return PolicyDecision(type="allow", reason="No approval-only pattern matched.", policy_name=self.name)
