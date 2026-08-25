from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import time

from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.observation import CommandResult, observation_from_command_result


class LocalCommandExecutor:
    """Development executor that runs bash actions on the host."""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore | None = None,
        artifact_threshold_bytes: int = 50_000,
        preview_chars: int = 12_000,
    ) -> None:
        self.artifacts = artifacts
        self.artifact_threshold_bytes = artifact_threshold_bytes
        self.preview_chars = preview_chars

    def run(self, action: BashAction, state: RunState) -> Observation:
        started = time.perf_counter()
        command_args = _local_shell_args(action.command)
        if command_args is None:
            return Observation(
                kind="policy_violation",
                stderr_preview=action.command,
                message=(
                    "Local command execution requested, but no bash executable was found. "
                    "Use Docker sandbox execution or install bash."
                ),
            )
        try:
            completed = subprocess.run(
                command_args,
                cwd=state.workspace_host_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=action.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            return observation_from_command_result(
                CommandResult(
                    exit_code=None,
                    stdout=_decode_timeout_output(exc.stdout),
                    stderr=_decode_timeout_output(exc.stderr),
                    timed_out=True,
                    timeout_sec=action.timeout_sec,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                ),
                state=state,
                artifacts=self.artifacts,
                artifact_threshold_bytes=self.artifact_threshold_bytes,
                preview_chars=self.preview_chars,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        return observation_from_command_result(
            CommandResult(
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration_ms=duration_ms,
            ),
            state=state,
            artifacts=self.artifacts,
            artifact_threshold_bytes=self.artifact_threshold_bytes,
            preview_chars=self.preview_chars,
        )


def _local_shell_args(command: str) -> list[str] | None:
    if sys.platform == "win32" and _uses_windows_native_build_tool(command):
        return ["cmd.exe", "/d", "/s", "/c", command]
    if sys.platform == "win32" and _uses_simple_python_command(command):
        parts = shlex.split(command, posix=True)
        return [sys.executable, *parts[1:]]
    bash_path = shutil.which("bash")
    if bash_path:
        return [bash_path, "-lc", _normalize_command_for_windows_bash(command)]
    return None


def _uses_windows_native_build_tool(command: str) -> bool:
    return re.match(
        r"^\s*(?:mvn(?:\.cmd)?|gradle(?:\.bat)?|gradlew(?:\.bat)?|javac|java|"
        r"\.\\mvnw(?:\.cmd)?|\.\\gradlew(?:\.bat)?)(?:\s|$)",
        command,
        flags=re.IGNORECASE,
    ) is not None


def _uses_simple_python_command(command: str) -> bool:
    if any(operator in command for operator in ("&", "|", ";", "<", ">")):
        return False
    return re.match(r"^\s*(?:python(?:\.exe|3)?|py)(?:\s|$)", command, flags=re.IGNORECASE) is not None


def _normalize_command_for_windows_bash(command: str) -> str:
    if sys.platform != "win32":
        return command
    normalized = re.sub(r"(^|[;&|()\s])python(?=\s|$)", r"\1python3", command)
    return re.sub(r"(?<![\w./-])mvn(?=\s)", "cmd.exe /c mvn", normalized)


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
