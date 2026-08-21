"""V4 runtime contracts shared by root and child runs.

The runtime layer is intentionally small: it authorizes capabilities before the
existing tool executor is reached and serializes workspace writes with a lease.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from minicc.core.protocol import BashAction, ToolCall

Capability = Literal["read", "edit", "write", "bash", "delegate"]


@dataclass(frozen=True)
class ChildCapabilities:
    profile: str
    tools: frozenset[Capability]
    read_only: bool = False
    can_delegate: bool = False

    @classmethod
    def for_profile(cls, profile: str) -> ChildCapabilities:
        profiles: dict[str, ChildCapabilities] = {
            "root": cls("root", frozenset({"read", "edit", "write", "bash", "delegate"}), can_delegate=True),
            "scout": cls("scout", frozenset({"read", "bash"}), read_only=True),
            "planner": cls("planner", frozenset({"read", "bash"}), read_only=True),
            "reviewer": cls("reviewer", frozenset({"read", "bash"}), read_only=True),
            "worker": cls("worker", frozenset({"read", "edit", "write", "bash"})),
        }
        try:
            return profiles[profile]
        except KeyError as exc:
            raise ValueError(f"Unknown capability profile: {profile}") from exc


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    code: str
    reason: str


class CapabilityPolicy:
    """Authorize a tool call before dispatching to any executor."""

    def decide(self, capabilities: ChildCapabilities, call: ToolCall) -> CapabilityDecision:
        tool = call.tool
        if tool not in capabilities.tools:
            return CapabilityDecision(False, "CAPABILITY_DENIED", f"{tool} is not allowed for {capabilities.profile}")
        return CapabilityDecision(True, "ALLOW", "capability allowed")

    def check_bash(self, capabilities: ChildCapabilities, action: BashAction) -> CapabilityDecision:
        if "bash" not in capabilities.tools:
            return CapabilityDecision(False, "CAPABILITY_DENIED", "bash is not allowed")
        if capabilities.read_only:
            return ReadOnlyBashPolicy().decide(action)
        return CapabilityDecision(True, "ALLOW", "bash allowed")


class ReadOnlyBashPolicy:
    """Conservative classifier for commands used by read-only child roles."""

    _deny_patterns = (
        r"(?:^|[\s;&|])(?:>>?|2>>?|<)",
        r"\|\s*(?:tee|dd)\b",
        r"\b(?:rm|mv|cp|install|mkdir|rmdir|touch|truncate|chmod|chown)\b",
        r"\b(?:pip|uv|npm|pnpm|yarn|poetry|cargo)\s+(?:install|add|remove|update)\b",
        r"\bgit\s+(?:add|commit|checkout|switch|reset|restore|clean|merge|rebase|apply|stash|mv|rm)\b",
        r"\b(?:sed|perl|python|python3|ruby|node)\b[^\n]*(?:-i|open\s*\(|write_text|write\s*\()",
    )

    def decide(self, action: BashAction) -> CapabilityDecision:
        command = action.command.strip()
        lower = command.lower()
        for pattern in self._deny_patterns:
            if re.search(pattern, lower):
                return CapabilityDecision(False, "READONLY_BASH_DENIED", "command may mutate the workspace")
        return CapabilityDecision(True, "ALLOW", "read-only bash command accepted")


@dataclass
class WorkspaceWriteLease:
    """A single-owner, fenced lease for workspace mutation."""

    owner_run_id: str | None = None
    owner_task_id: str | None = None
    lease_epoch: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def acquire(self, run_id: str, task_id: str) -> int | None:
        with self._lock:
            if self.owner_run_id is not None:
                return None
            self.lease_epoch += 1
            self.owner_run_id = run_id
            self.owner_task_id = task_id
            return self.lease_epoch

    def release(self, run_id: str, task_id: str, epoch: int | None = None) -> bool:
        with self._lock:
            if (self.owner_run_id, self.owner_task_id) != (run_id, task_id):
                return False
            if epoch is not None and epoch != self.lease_epoch:
                return False
            self.owner_run_id = None
            self.owner_task_id = None
            return True

    def allows(self, run_id: str, task_id: str, epoch: int | None = None) -> bool:
        with self._lock:
            return (
                (self.owner_run_id, self.owner_task_id) == (run_id, task_id)
                and (epoch is None or epoch == self.lease_epoch)
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"owner_run_id": self.owner_run_id, "owner_task_id": self.owner_task_id, "lease_epoch": self.lease_epoch}


def workspace_fingerprint(root: Path) -> str:
    """Return a deterministic content fingerprint for mutation detection."""
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode())
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


class AgentRuntime:
    """Authorization seam used by tool runners and child orchestration."""

    def __init__(self, capabilities: ChildCapabilities, *, lease: WorkspaceWriteLease | None = None) -> None:
        self.capabilities = capabilities
        self.lease = lease
        self.policy = CapabilityPolicy()

    def authorize(self, call: ToolCall, *, run_id: str | None = None, task_id: str | None = None, epoch: int | None = None) -> CapabilityDecision:
        decision = self.policy.decide(self.capabilities, call)
        if not decision.allowed:
            return decision
        if call.tool in {"edit", "write", "bash"} and self.capabilities.read_only:
            return CapabilityDecision(False, "CAPABILITY_DENIED", "read-only child cannot mutate workspace")
        if call.tool in {"edit", "write", "bash"} and self.lease is not None and not self.lease.allows(run_id or "", task_id or "", epoch):
            return CapabilityDecision(False, "WRITE_LEASE_DENIED", "workspace write lease is not held")
        return decision
