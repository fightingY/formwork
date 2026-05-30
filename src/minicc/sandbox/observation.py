from __future__ import annotations

from dataclasses import asdict, dataclass

from minicc.core.state import Observation, RunState
from minicc.sandbox.artifact_store import ArtifactStore, preview_text


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    timeout_sec: int | None = None


def observation_from_command_result(
    result: CommandResult,
    *,
    state: RunState,
    artifacts: ArtifactStore | None = None,
    artifact_threshold_bytes: int = 16 * 1024,
    preview_chars: int = 12_000,
) -> Observation:
    artifact_ids: list[str] = []
    stdout_preview = _preview_or_artifact(
        "stdout",
        result.stdout,
        state=state,
        artifacts=artifacts,
        artifact_ids=artifact_ids,
        artifact_threshold_bytes=artifact_threshold_bytes,
        preview_chars=preview_chars,
    )
    stderr_preview = _preview_or_artifact(
        "stderr",
        result.stderr,
        state=state,
        artifacts=artifacts,
        artifact_ids=artifact_ids,
        artifact_threshold_bytes=artifact_threshold_bytes,
        preview_chars=preview_chars,
    )

    if result.timed_out:
        return Observation(
            kind="timeout",
            exit_code=result.exit_code,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            artifact_ids=artifact_ids,
            message=f"Command timed out after {result.timeout_sec} seconds.",
            duration_ms=result.duration_ms,
        )

    if result.exit_code == 0 and not result.stdout and not result.stderr:
        return Observation(
            kind="no_output",
            exit_code=0,
            artifact_ids=artifact_ids,
            message="Command exited successfully with no output.",
            duration_ms=result.duration_ms,
        )

    if result.exit_code == 0:
        return Observation(
            kind="command_result",
            exit_code=0,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            artifact_ids=artifact_ids,
            message="Command exited successfully.",
            duration_ms=result.duration_ms,
        )

    return Observation(
        kind="command_error",
        exit_code=result.exit_code,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        artifact_ids=artifact_ids,
        message=f"Command failed with exit code {result.exit_code}.",
        duration_ms=result.duration_ms,
    )


def _preview_or_artifact(
    artifact_type: str,
    content: str,
    *,
    state: RunState,
    artifacts: ArtifactStore | None,
    artifact_ids: list[str],
    artifact_threshold_bytes: int,
    preview_chars: int,
) -> str:
    if not content:
        return ""

    content_bytes = len(content.encode("utf-8", errors="replace"))
    if artifacts is None or content_bytes <= artifact_threshold_bytes:
        return preview_text(content, preview_chars)

    artifact = artifacts.write_text(artifact_type, content)
    state.artifacts.append(asdict(artifact))
    state.metrics["artifact_bytes"] = state.metrics.get("artifact_bytes", 0) + artifact.bytes
    artifact_ids.append(artifact.id)
    return (
        f"[{artifact_type} exceeded {artifact_threshold_bytes} bytes; "
        f"full output saved as {artifact.id} at {artifact.path}; "
        f"bytes={artifact.bytes}]\n"
        f"{artifact.preview}"
    )
