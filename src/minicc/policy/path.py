from __future__ import annotations

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.policy.base import PolicyDecision


class PathPolicy:
    name = "PathPolicy"

    def __init__(self, sensitive_paths: list[str] | None = None) -> None:
        self.sensitive_paths = sensitive_paths or [
            "/host",
            "/mnt",
            "/var/run/docker.sock",
            "/root/.ssh",
            "~/.ssh",
        ]

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        command = action.command
        for path in self.sensitive_paths:
            if path in command:
                return PolicyDecision(
                    type="deny",
                    reason=f"Command references sensitive path {path}.",
                    policy_name=self.name,
                )
        return PolicyDecision(type="allow", reason="No sensitive path references found.", policy_name=self.name)
