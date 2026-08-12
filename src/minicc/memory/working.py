from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from minicc.core.protocol import MemoryReference
from minicc.core.state import RunState

WORKING_MEMORY_SCHEMA_VERSION = 1
MAX_EXCERPT_CHARS = 4_000


class WorkingMemoryError(ValueError):
    """A working-memory snapshot cannot be trusted or attached."""


def ground_memory_references(
    state: RunState,
    references: Iterable[MemoryReference],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workspace = state.workspace_host_path.resolve() if state.workspace_host_path else None
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for reference in references:
        if workspace is None:
            rejected.append({"path": reference.path, "reason": "run_has_no_workspace"})
            continue
        target = (workspace / reference.path).resolve()
        reason = ""
        try:
            target.relative_to(workspace)
        except ValueError:
            reason = "path_escapes_workspace"
        if not reason and (not target.is_file() or target.is_symlink()):
            reason = "source_is_not_a_regular_file"
        if reason:
            rejected.append({"path": reference.path, "reason": reason})
            continue
        raw = target.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if reference.line_end > len(lines):
            rejected.append({"path": reference.path, "reason": "line_range_out_of_bounds"})
            continue
        excerpt = "\n".join(lines[reference.line_start - 1 : reference.line_end])
        if not excerpt.strip() or len(excerpt) > MAX_EXCERPT_CHARS:
            rejected.append({"path": reference.path, "reason": "excerpt_empty_or_too_large"})
            continue
        accepted.append(
            {
                "path": reference.path,
                "line_start": reference.line_start,
                "line_end": reference.line_end,
                "excerpt": excerpt,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            }
        )
    return accepted, rejected


def write_working_memory_snapshot(state: RunState) -> Path | None:
    if state.run_dir is None or not state.memory_references:
        return None
    project_digest = _project_digest(state.run_dir)
    if not project_digest:
        return None
    payload: dict[str, Any] = {
        "schema_version": WORKING_MEMORY_SCHEMA_VERSION,
        "source_run_id": state.run_id,
        "project_content_sha256": project_digest,
        "items": state.memory_references,
    }
    payload["payload_sha256"] = _payload_sha256(payload)
    path = state.run_dir / "working_memory.json"
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def attach_working_memory(
    state: RunState,
    source_run_id: str,
    *,
    runs_root: Path | None = None,
) -> None:
    if state.run_dir is None:
        raise WorkingMemoryError("follow-up run has no run directory")
    root = (runs_root or state.run_dir.parent).resolve()
    source_dir = (root / source_run_id).resolve()
    if source_dir.parent != root:
        raise WorkingMemoryError("working-memory source run id is unsafe")
    path = source_dir / "working_memory.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkingMemoryError(f"working-memory snapshot is unavailable: {source_run_id}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != WORKING_MEMORY_SCHEMA_VERSION:
        raise WorkingMemoryError("working-memory snapshot schema is unsupported")
    if payload.get("source_run_id") != source_run_id:
        raise WorkingMemoryError("working-memory source run identity does not match")
    if payload.get("payload_sha256") != _payload_sha256(payload):
        raise WorkingMemoryError("working-memory snapshot integrity check failed")
    current_digest = _project_digest(state.run_dir)
    if not current_digest or payload.get("project_content_sha256") != current_digest:
        raise WorkingMemoryError("working-memory project snapshot does not match follow-up workspace")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise WorkingMemoryError("working-memory snapshot has no grounded items")
    items = [dict(item) for item in raw_items if _valid_item(item)]
    if len(items) != len(raw_items):
        raise WorkingMemoryError("working-memory snapshot contains invalid items")
    _verify_items_against_workspace(state, items)
    state.working_memory_source_run_id = source_run_id
    state.working_memory = items
    state.metrics["working_memory_candidates"] = len(items)


def working_memory_context(state: RunState) -> str:
    if not state.working_memory:
        return ""
    lines = [
        f"Explicit follow-up working memory from run {state.working_memory_source_run_id}:",
        "These are immutable source excerpts. Use them when relevant; inspect the file again if the current workspace contradicts them.",
    ]
    for item in state.working_memory:
        lines.append(
            f"- {item['path']}:{item['line_start']}-{item['line_end']} "
            f"(file_sha256={item['file_sha256']}):\n{item['excerpt']}"
        )
    return "\n".join(lines)


def _project_digest(run_dir: Path) -> str:
    manifest_path = run_dir / "workspace_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    included = manifest.get("included") if isinstance(manifest, dict) else None
    return str(included.get("content_digest_sha256") or "") if isinstance(included, dict) else ""


def _valid_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "path",
        "line_start",
        "line_end",
        "excerpt",
        "file_sha256",
        "excerpt_sha256",
    }
    if set(value) != required:
        return False
    excerpt = value.get("excerpt")
    path = value.get("path")
    return (
        isinstance(path, str)
        and _safe_relative_path(path)
        and isinstance(value.get("line_start"), int)
        and isinstance(value.get("line_end"), int)
        and isinstance(excerpt, str)
        and bool(excerpt.strip())
        and hashlib.sha256(excerpt.encode("utf-8")).hexdigest() == value.get("excerpt_sha256")
        and _is_sha256(value.get("file_sha256"))
    )


def _verify_items_against_workspace(state: RunState, items: list[dict[str, Any]]) -> None:
    if state.workspace_host_path is None:
        raise WorkingMemoryError("follow-up run has no workspace for source verification")
    workspace = state.workspace_host_path.resolve()
    for item in items:
        target = (workspace / item["path"]).resolve()
        try:
            target.relative_to(workspace)
        except ValueError as exc:
            raise WorkingMemoryError("working-memory source path escapes follow-up workspace") from exc
        if not target.is_file() or target.is_symlink():
            raise WorkingMemoryError("working-memory source evidence is missing in follow-up workspace")
        if hashlib.sha256(target.read_bytes()).hexdigest() != item["file_sha256"]:
            raise WorkingMemoryError("working-memory source evidence changed in follow-up workspace")


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/").strip().strip("/")
    return bool(
        normalized
        and not value.startswith(("/", "\\"))
        and re.match(r"^[A-Za-z]:", normalized) is None
        and ".." not in PurePosixPath(normalized).parts
        and "." not in PurePosixPath(normalized).parts
    )


def _payload_sha256(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
