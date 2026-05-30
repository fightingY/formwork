from __future__ import annotations

import re

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.policy.base import PolicyDecision


class NetworkPolicy:
    name = "NetworkPolicy"

    def __init__(
        self,
        *,
        mode: str = "locked",
        require_approval: bool = True,
    ) -> None:
        self.mode = mode
        self.require_approval = require_approval

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        if self.mode != "locked":
            return PolicyDecision(type="allow", reason="Network policy is not locked.", policy_name=self.name)

        if not _looks_like_network_action(action.command):
            return PolicyDecision(type="allow", reason="No network command detected.", policy_name=self.name)

        reason = "Network-related command is blocked in locked sandbox mode."
        if self.require_approval:
            return PolicyDecision(
                type="require_approval",
                reason=reason,
                approval_question=(
                    "This action may require network access in locked mode. "
                    f"Approve running it? Command: {action.command}"
                ),
                policy_name=self.name,
            )
        return PolicyDecision(type="deny", reason=reason, policy_name=self.name)


def _looks_like_network_action(command: str) -> bool:
    patterns = [
        r"\bcurl\b",
        r"\bwget\b",
        r"\bgit\s+clone\b",
        r"\bpip\s+install\b",
        r"\bpython\s+-m\s+pip\s+install\b",
        r"\buv\s+(sync|add|pip\s+install)\b",
        r"\bnpm\s+(install|i)\b",
        r"\bpnpm\s+(install|add)\b",
        r"\byarn\s+(install|add)\b",
        r"\bpoetry\s+add\b",
        r"\bapt(-get)?\s+(update|install)\b",
        r"\bapk\s+add\b",
    ]
    lowered = command.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)
