from __future__ import annotations

import re

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.policy.base import PolicyDecision


class CommandPolicy:
    name = "CommandPolicy"

    def __init__(self, *, deny_sudo: bool = True) -> None:
        self.deny_sudo = deny_sudo

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        command = action.command.strip()
        lower = command.lower()

        if self.deny_sudo and _contains_command(lower, "sudo"):
            return self._deny("sudo is not allowed in the sandbox.", action)

        dangerous_patterns = [
            r"\brm\s+-[^\n;|&]*[rf][^\n;|&]*\s+/",
            r"\brm\s+-[^\n;|&]*[rf][^\n;|&]*\s+\*",
            r"\bshutdown\b",
            r"\breboot\b",
            r"\bhalt\b",
            r"\bpoweroff\b",
            r"\bmkfs(?:\.[a-z0-9]+)?\b",
            r"\bmount\b",
            r"\bumount\b",
            r"\bchmod\s+-r\s+777\s+/",
            r"\bdd\s+.*\bof=/dev/",
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, lower):
                return self._deny("Command matches a dangerous system operation pattern.", action)

        return PolicyDecision(type="allow", reason="Command did not match dangerous patterns.", policy_name=self.name)

    def _deny(self, reason: str, action: BashAction) -> PolicyDecision:
        return PolicyDecision(
            type="deny",
            reason=f"{reason} Command was not executed: {action.command}",
            policy_name=self.name,
        )


def _contains_command(command: str, name: str) -> bool:
    return re.search(rf"(^|[;&|()\s]){re.escape(name)}(\s|$)", command) is not None
