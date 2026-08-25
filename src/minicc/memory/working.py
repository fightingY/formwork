from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from minicc.core.protocol import MemoryReference
from minicc.core.state import RunState

WORKING_MEMORY_SCHEMA_VERSION = 2
MAX_EXCERPT_CHARS = 4_000

# V5.1 P4 (docs/V5_1_MEMORY_REDESIGN_PLAN.md): the four-fold SHA ceremony is gone.
# Working memory is an optional, model-declared excerpt cue — not evidence.  The
# file / excerpt / payload / project digests turned that best-effort enhancement
# into an abort (WorkingMemoryError), so they are removed.  Everything here now
# *degrades*: grounding rejects unverifiable references at capture time, the
# snapshot carries no self-hashes, and adoption validates only structure +
# identity — a stale or incompatible snapshot simply fails to attach and records
# `working_memory_invalid_adoptions` instead of raising.  The trace/ledger SHA
# anchoring is a separate subsystem and is untouched.


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
            }
        )
    return accepted, rejected


def write_working_memory_snapshot(state: RunState) -> Path | None:
    if state.run_dir is None or not state.memory_references:
        return None
    payload: dict[str, Any] = {
        "schema_version": WORKING_MEMORY_SCHEMA_VERSION,
        "source_run_id": state.run_id,
        "items": state.memory_references,
    }
    path = state.run_dir / "working_memory.json"
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def attach_working_memory(
    state: RunState,
    source_run_id: str,
    *,
    runs_root: Path | None = None,
) -> None:
    """Adopt a follow-up snapshot, or fail-skip without raising.

    Adoption is best-effort: any problem (missing run dir, unsafe source id,
    unavailable/incompatible snapshot, no valid items) records
    ``working_memory_invalid_adoptions`` and returns, leaving the run's working
    memory empty.  It never aborts the run.
    """
    if state.run_dir is None:
        _reject(state)
        return
    root = (runs_root or state.run_dir.parent).resolve()
    source_dir = (root / source_run_id).resolve()
    if source_dir.parent != root:
        _reject(state)
        return
    path = source_dir / "working_memory.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _reject(state)
        return
    if not isinstance(payload, dict) or payload.get("schema_version") != WORKING_MEMORY_SCHEMA_VERSION:
        _reject(state)
        return
    if payload.get("source_run_id") != source_run_id:
        _reject(state)
        return
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        _reject(state)
        return
    items = [dict(item) for item in raw_items if _valid_item(item)]
    if not items:
        _reject(state)
        return
    state.working_memory_source_run_id = source_run_id
    state.working_memory = items
    state.metrics["working_memory_candidates"] = len(items)


def working_memory_context(state: RunState) -> str:
    if not state.working_memory:
        return ""
    lines = [
        f"Explicit follow-up working memory from run {state.working_memory_source_run_id}:",
        "These are source excerpts. Use them when relevant; inspect the file again if the current workspace contradicts them.",
    ]
    for item in state.working_memory:
        lines.append(
            f"- {item['path']}:{item['line_start']}-{item['line_end']}:\n{item['excerpt']}"
        )
    return "\n".join(lines)


def _valid_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"path", "line_start", "line_end", "excerpt"}
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
    )


def _reject(state: RunState) -> None:
    state.metrics["working_memory_invalid_adoptions"] = (
        int(state.metrics.get("working_memory_invalid_adoptions") or 0) + 1
    )


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/").strip().strip("/")
    return bool(
        normalized
        and not value.startswith(("/", "\\"))
        and re.match(r"^[A-Za-z]:", normalized) is None
        and ".." not in PurePosixPath(normalized).parts
        and "." not in PurePosixPath(normalized).parts
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)