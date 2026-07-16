from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minicc.core.state import (
    RunState,
    TrajectoryStep,
    load_run_state,
    save_run_state,
    trajectory_step_from_dict,
    trajectory_step_to_dict,
)
from minicc.trace.recorder import TraceRecorder


CHECKPOINT_SCHEMA_VERSION = 1
_IGNORED_WORKSPACE_PARTS = {
    ".git",
    ".minicc_artifacts",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


class CheckpointError(RuntimeError):
    """Base error for checkpoint creation or restoration failures."""


class CheckpointDriftError(CheckpointError):
    """The checkpoint no longer matches its run or workspace."""


class AmbiguousExecutionError(CheckpointError):
    """An action may have executed but has no durable completion record."""


@dataclass(frozen=True)
class RestoredCheckpoint:
    state: RunState
    trajectory: list[TrajectoryStep]
    checkpoint_id: str
    reason: str


class CheckpointManager:
    def __init__(self, run_dir: Path, *, trace: TraceRecorder | None = None) -> None:
        self.run_dir = run_dir.resolve()
        self.checkpoints_dir = self.run_dir / "checkpoints"
        self.trace = trace

    def create(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
        *,
        reason: str,
    ) -> Path:
        workspace = _require_workspace(state)
        _validate_run_binding(state, self.run_dir)
        sequence = state.checkpoint_sequence + 1
        checkpoint_id = f"checkpoint-{sequence:04d}"
        state.checkpoint_sequence = sequence
        state.latest_checkpoint_id = checkpoint_id
        state.metrics["checkpoints_created"] = state.metrics.get("checkpoints_created", 0) + 1
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": checkpoint_id,
            "sequence": sequence,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "run_id": state.run_id,
            "run_dir": str(self.run_dir),
            "workspace_path": str(workspace),
            "workspace_fingerprint": workspace_fingerprint(workspace),
            "state": state.to_dict(),
            "trajectory": [trajectory_step_to_dict(step) for step in trajectory],
        }
        encoded = _canonical_json(payload)
        checkpoint_path = self.checkpoints_dir / f"{checkpoint_id}.json"
        _atomic_write(checkpoint_path, json.dumps(payload, ensure_ascii=False, indent=2))
        pointer = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_id": state.run_id,
            "checkpoint_id": checkpoint_id,
            "checkpoint_file": checkpoint_path.name,
            "checkpoint_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        _atomic_write(self.checkpoints_dir / "latest.json", json.dumps(pointer, ensure_ascii=False, indent=2))
        save_run_state(state)
        if self.trace is not None:
            self.trace.checkpoint_created(state, checkpoint_id, reason)
        return checkpoint_path

    def restore_latest(self, run_id: str) -> RestoredCheckpoint:
        pointer_path = self.checkpoints_dir / "latest.json"
        if not pointer_path.exists():
            raise CheckpointError(f"No checkpoint exists for run {run_id}.")
        pointer = _read_json(pointer_path)
        if pointer.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointDriftError("Checkpoint pointer schema version is not supported.")
        if pointer.get("run_id") != run_id:
            raise CheckpointDriftError("Checkpoint pointer belongs to a different run.")
        checkpoint_name = str(pointer.get("checkpoint_file") or "")
        checkpoint_path = (self.checkpoints_dir / checkpoint_name).resolve()
        if checkpoint_path.parent != self.checkpoints_dir.resolve() or not checkpoint_path.exists():
            raise CheckpointDriftError("Checkpoint file is missing or outside the run checkpoint directory.")
        payload = _read_json(checkpoint_path)
        actual_digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
        if actual_digest != pointer.get("checkpoint_sha256"):
            raise CheckpointDriftError("Checkpoint content digest does not match latest.json.")
        if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointDriftError("Checkpoint schema version is not supported.")
        if payload.get("run_id") != run_id or Path(str(payload.get("run_dir"))).resolve() != self.run_dir:
            raise CheckpointDriftError("Checkpoint run identity does not match the requested run.")
        raw_state = payload.get("state")
        if not isinstance(raw_state, dict):
            raise CheckpointDriftError("Checkpoint state payload is missing.")
        state = RunState.from_dict(raw_state)
        _validate_run_binding(state, self.run_dir)
        live_state_path = self.run_dir / "state.json"
        if live_state_path.exists():
            live_state = load_run_state(live_state_path)
            _validate_run_binding(live_state, self.run_dir)
            if live_state.workspace_host_path != state.workspace_host_path:
                raise CheckpointDriftError("Live run state points to a different workspace than the checkpoint.")
            if any(entry.get("status") == "started" for entry in live_state.execution_journal):
                raise AmbiguousExecutionError(
                    "Live run state contains an action with ambiguous execution status; automatic replay is blocked."
                )
        workspace = _require_workspace(state)
        if str(workspace) != str(Path(str(payload.get("workspace_path"))).resolve()):
            raise CheckpointDriftError("Checkpoint workspace path does not match saved state.")
        if workspace_fingerprint(workspace) != payload.get("workspace_fingerprint"):
            state.metrics["resume_drift_errors"] = state.metrics.get("resume_drift_errors", 0) + 1
            raise CheckpointDriftError("Workspace content changed after the latest checkpoint.")
        if any(entry.get("status") == "started" for entry in state.execution_journal):
            raise AmbiguousExecutionError(
                "The latest checkpoint contains an action with ambiguous execution status; automatic replay is blocked."
            )
        if state.status in {"completed", "failed", "waiting_approval"}:
            raise CheckpointError(f"Checkpoint status {state.status} is not resumable through checkpoint recovery.")
        raw_trajectory = payload.get("trajectory")
        if not isinstance(raw_trajectory, list):
            raise CheckpointDriftError("Checkpoint trajectory payload is missing.")
        trajectory = [trajectory_step_from_dict(item) for item in raw_trajectory if isinstance(item, dict)]
        state.status = "running"
        state.container_name = None
        state.resume_count += 1
        state.metrics["resumes_completed"] = state.metrics.get("resumes_completed", 0) + 1
        if self.trace is not None:
            self.trace.checkpoint_restored(state, str(payload.get("checkpoint_id")))
        return RestoredCheckpoint(
            state=state,
            trajectory=trajectory,
            checkpoint_id=str(payload.get("checkpoint_id")),
            reason=str(payload.get("reason") or ""),
        )


def workspace_fingerprint(workspace: Path) -> str:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise CheckpointDriftError(f"Workspace does not exist: {workspace}")
    digest = hashlib.sha256()
    for path in sorted((item for item in workspace.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(workspace)
        if any(part in _IGNORED_WORKSPACE_PARTS for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_run_binding(state: RunState, run_dir: Path) -> None:
    if state.run_dir is None or state.run_dir.resolve() != run_dir.resolve():
        raise CheckpointDriftError("Run state points to a different run directory.")


def _require_workspace(state: RunState) -> Path:
    if state.workspace_host_path is None:
        raise CheckpointError("Run state has no workspace path.")
    workspace = state.workspace_host_path.resolve()
    if not workspace.is_dir():
        raise CheckpointDriftError(f"Workspace does not exist: {workspace}")
    return workspace


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointDriftError(f"Checkpoint JSON is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise CheckpointDriftError(f"Checkpoint JSON must contain an object: {path}")
    return payload


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
