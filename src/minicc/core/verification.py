from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState

VerificationExecutor = Callable[[BashAction], Observation]


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    observation: Observation | None = None
    reason: str = ""


class CompletionVerifier(Protocol):
    def verify(
        self,
        state: RunState,
        execute: VerificationExecutor,
    ) -> VerificationResult:
        ...


@dataclass(frozen=True)
class CommandCompletionVerifier:
    commands: tuple[str, ...]
    timeout_sec: int = 120

    def verify(
        self,
        state: RunState,
        execute: VerificationExecutor,
    ) -> VerificationResult:
        del state
        if not self.commands:
            return VerificationResult(False, reason="Completion verifier has no commands.")
        for command in self.commands:
            observation = execute(
                BashAction(
                    command=command,
                    timeout_sec=self.timeout_sec,
                    purpose="authoritative completion verification",
                )
            )
            if observation.exit_code != 0 or observation.kind not in {"command_result", "no_output"}:
                return VerificationResult(
                    False,
                    observation=Observation(
                        kind="verification_error",
                        exit_code=observation.exit_code,
                        stdout_preview=observation.stdout_preview,
                        stderr_preview=observation.stderr_preview,
                        artifact_ids=list(observation.artifact_ids),
                        message=(
                            f"Completion verifier failed for `{command}`. "
                            f"{observation.message or observation.kind}"
                        ),
                        duration_ms=observation.duration_ms,
                    ),
                    reason=f"Verifier command failed: {command}",
                )
        return VerificationResult(True, reason="All completion verifier commands passed.")
