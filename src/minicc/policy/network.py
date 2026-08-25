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
    command_code = _strip_heredoc_bodies(command)
    boundary = r"(?:^[ \t]*|[;&|][ \t]*)"
    prefixes = r"(?:[A-Za-z_][A-Za-z0-9_]*=\S+[ \t]+)*(?:sudo[ \t]+)?"
    patterns = [
        r"curl\b",
        r"wget\b",
        r"git\s+clone\b",
        r"pip\s+install\b",
        r"python\s+-m\s+pip\s+install\b",
        r"uv\s+(sync|add|pip\s+install)\b",
        r"npm\s+(install|i)\b",
        r"pnpm\s+(install|add)\b",
        r"yarn\s+(install|add)\b",
        r"poetry\s+add\b",
        r"apt(-get)?\s+(update|install)\b",
        r"apk\s+add\b",
    ]
    lowered = command_code.lower()
    if any(re.search(boundary + prefixes + pattern, lowered, re.MULTILINE) for pattern in patterns):
        return True

    shell_wrapper = re.compile(
        boundary + prefixes + r"(?:bash|sh|zsh)[ \t]+-c[ \t]+(['\"])(.*?)\1",
        re.MULTILINE | re.DOTALL,
    )
    return any(_looks_like_network_action(match.group(2)) for match in shell_wrapper.finditer(command_code))


def _strip_heredoc_bodies(command: str) -> str:
    """Keep shell syntax while excluding literal here-doc payloads from policy matching."""
    lines = command.splitlines()
    kept: list[str] = []
    delimiter: str | None = None
    declaration = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        match = declaration.search(line)
        if match:
            delimiter = match.group(2)
    return "\n".join(kept)
