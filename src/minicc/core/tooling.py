from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from minicc.core.multi_agent import WorkspaceLeaseRegistry
from minicc.core.protocol import DelegateAction, ToolCall, bash_action_from_tool_call
from minicc.core.state import Observation, RunState

ExecutionMode = Literal["parallel", "exclusive"]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool: str
    model_order: int
    execution_mode: ExecutionMode
    content: dict[str, Any]
    is_error: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int = 0

    def observation(self) -> Observation:
        return Observation(
            kind="command_error" if self.is_error else "command_result",
            exit_code=None if self.is_error else 0,
            stdout_preview=json.dumps(self.content, ensure_ascii=False),
            message=self.content.get("error", "") if self.is_error else "",
            duration_ms=self.duration_ms,
        )


class ToolRunner(Protocol):
    def run(self, call: ToolCall, state: RunState) -> ToolResult:
        ...


def _error(call: ToolCall, order: int, mode: ExecutionMode, code: str, message: str) -> ToolResult:
    return ToolResult(call.id, call.tool, order, mode, {"error_code": code, "error": message}, True)


class FileSystemCapability:
    """Workspace-bound read/edit/write capability with optimistic version checks."""

    def __init__(self, *, max_read_lines: int = 400, max_read_chars: int = 30_000) -> None:
        self.max_read_lines = max_read_lines
        self.max_read_chars = max_read_chars

    def execution_mode(self, call: ToolCall) -> ExecutionMode:
        return "parallel" if call.tool in {"read", "delegate"} else "exclusive"

    def run(self, call: ToolCall, state: RunState) -> ToolResult:
        order = 0
        mode: ExecutionMode = "parallel" if call.tool in {"read", "delegate"} else "exclusive"
        if state.capability_profile == "scout" and call.tool in {"edit", "write", "bash"}:
            return _error(call, order, mode, "CAPABILITY_DENIED", "scout children are read-only; edit/write/bash are unavailable")
        try:
            if call.tool == "read":
                return ToolResult(call.id, call.tool, order, mode, self.read(state, call.arguments))
            if call.tool == "edit":
                with _workspace_write_lease(state):
                    return ToolResult(call.id, call.tool, order, mode, self.edit(state, call.arguments))
            if call.tool == "write":
                with _workspace_write_lease(state):
                    return ToolResult(call.id, call.tool, order, mode, self.write(state, call.arguments))
            return _error(call, order, mode, "UNKNOWN_TOOL", f"Unsupported FS tool: {call.tool}")
        except ToolInputError as exc:
            return _error(call, order, mode, exc.code, str(exc))
        except OSError as exc:
            return _error(call, order, mode, "FS_RUNTIME_ERROR", str(exc))

    def read(self, state: RunState, arguments: dict[str, Any] | Any) -> dict[str, Any]:
        path = self._resolve(state, arguments.get("path"))
        if not path.exists() or not path.is_file():
            raise ToolInputError("READ_NOT_FOUND", f"File does not exist: {arguments.get('path')}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = _positive_int(arguments.get("offset", 1), "offset")
        limit = _positive_int(arguments.get("limit", self.max_read_lines), "limit")
        limit = min(limit, self.max_read_lines)
        start = offset - 1
        selected = lines[start : start + limit]
        text = "\n".join(f"{start + index + 1}: {line}" for index, line in enumerate(selected))
        truncated = start + len(selected) < len(lines)
        if len(text) > self.max_read_chars:
            text = text[: self.max_read_chars]
            truncated = True
        return {
            "path": self._relative(state, path),
            "content": text,
            "offset": offset,
            "limit": limit,
            "total_lines": len(lines),
            "truncated": truncated,
        }

    def edit(self, state: RunState, arguments: dict[str, Any] | Any) -> dict[str, Any]:
        path = self._resolve(state, arguments.get("path"))
        if not path.exists() or not path.is_file():
            raise ToolInputError("EDIT_NOT_FOUND", f"File does not exist: {arguments.get('path')}")
        expected = arguments.get("expected_hash")
        if not isinstance(expected, str) or not expected:
            raise ToolInputError("EDIT_EXPECTED_HASH_REQUIRED", "edit.expected_hash is required for existing files")
        before_hash = _sha256(path)
        if expected != before_hash:
            raise ToolInputError("EDIT_VERSION_CONFLICT", "File version does not match expected_hash")
        old = arguments.get("old_string")
        new = arguments.get("new_string")
        if not isinstance(old, str) or not old:
            raise ToolInputError("EDIT_OLD_STRING_REQUIRED", "edit.old_string must be non-empty")
        if not isinstance(new, str):
            raise ToolInputError("EDIT_NEW_STRING_REQUIRED", "edit.new_string must be a string")
        original = path.read_text(encoding="utf-8")
        count = original.count(old)
        replace_all = bool(arguments.get("replace_all", False))
        if count == 0:
            raise ToolInputError("EDIT_MATCH_NOT_FOUND", "old_string was not found")
        if count != 1 and not replace_all:
            raise ToolInputError("EDIT_MATCH_NOT_UNIQUE", "old_string matched more than once")
        updated = original.replace(old, new, -1 if replace_all else 1)
        _atomic_write(path, updated)
        after_hash = _sha256(path)
        return {
            "path": self._relative(state, path),
            "old_hash": before_hash,
            "new_hash": after_hash,
            "changed_lines": abs(updated.count("\n") - original.count("\n")),
            "diff": "".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True), fromfile=str(path), tofile=str(path))),
        }

    def write(self, state: RunState, arguments: dict[str, Any] | Any) -> dict[str, Any]:
        path = self._resolve(state, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolInputError("WRITE_CONTENT_REQUIRED", "write.content must be a string")
        existed = path.exists()
        old_hash = _sha256(path) if existed and path.is_file() else None
        if existed:
            expected = arguments.get("expected_hash")
            if not isinstance(expected, str) or not expected:
                raise ToolInputError("WRITE_EXPECTED_HASH_REQUIRED", "write.expected_hash is required for existing files")
            if expected != old_hash:
                raise ToolInputError("WRITE_VERSION_CONFLICT", "File version does not match expected_hash")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, content)
        new_hash = _sha256(path)
        return {
            "path": self._relative(state, path),
            "created": not existed,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "bytes": len(content.encode("utf-8")),
        }

    def _resolve(self, state: RunState, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolInputError("PATH_REQUIRED", "path must be a non-empty relative path")
        root = (state.workspace_host_path or Path.cwd()).resolve()
        candidate = Path(raw.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
            raise ToolInputError("PATH_OUT_OF_BOUNDS", "path must be a safe relative path")
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ToolInputError("PATH_OUT_OF_BOUNDS", "path escapes workspace") from exc
        return resolved

    def _relative(self, state: RunState, path: Path) -> str:
        return path.relative_to((state.workspace_host_path or Path.cwd()).resolve()).as_posix()


class ToolInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HybridToolRunner:
    def __init__(self, bash_executor: Any, fs: FileSystemCapability | None = None) -> None:
        self.bash_executor = bash_executor
        self.fs = fs or FileSystemCapability()
        self.action_handler: Any | None = None

    def execution_mode(self, call: ToolCall) -> ExecutionMode:
        return "parallel" if call.tool == "read" else "exclusive"

    def run(self, call: ToolCall, state: RunState) -> ToolResult:
        mode: ExecutionMode = "parallel" if call.tool == "read" else "exclusive"
        if state.capability_profile == "scout" and call.tool in {"edit", "write", "bash"}:
            return _error(call, 0, mode, "CAPABILITY_DENIED", "scout children are read-only; edit/write/bash are unavailable")
        if call.tool in {"read", "edit", "write"}:
            result = self.fs.run(call, state)
            return ToolResult(result.call_id, result.tool, result.model_order, mode, result.content, result.is_error)
        if call.tool == "delegate":
            if self.action_handler is None or self.action_handler.multi_agent_manager is None:
                return _error(call, 0, mode, "DELEGATE_UNAVAILABLE", "delegate is unavailable")
            args = dict(call.arguments)
            action = DelegateAction(
                tasks=tuple(dict(item) for item in args.get("tasks", []) if isinstance(item, dict)),
                join=str(args.get("join", "all")),
                background=bool(args.get("background", False)),
            )
            outcome = self.action_handler.handle(action, state)
            observation = outcome.steps[-1].observation if outcome.steps else state.last_observation
            if observation is None:
                return _error(call, 0, mode, "DELEGATE_EMPTY", "delegate produced no observation")
            content = {
                "kind": observation.kind,
                "message": observation.message,
                "stdout": observation.stdout_preview,
                "stderr": observation.stderr_preview,
                "exit_code": observation.exit_code,
            }
            return ToolResult(call.id, call.tool, 0, mode, content, observation.kind not in {"command_result", "no_output"})
        if call.tool == "bash":
            action = bash_action_from_tool_call(call)
            with _workspace_write_lease(state):
                if self.action_handler is not None:
                    outcome = self.action_handler.handle(action, state)
                else:
                    observation = self.bash_executor.run(action, state)
                    outcome = None
            if outcome is not None:
                if state.status == "waiting_approval":
                    observation = Observation(
                        kind="approval_result",
                        message="Bash action is waiting for approval and was not executed.",
                    )
                elif outcome.steps:
                    observation = outcome.steps[-1].observation
                elif state.last_observation is not None:
                    observation = state.last_observation
                else:
                    observation = Observation(
                        kind="policy_violation",
                        message="Bash action did not produce an observation.",
                    )
            content = {
                "exit_code": observation.exit_code,
                "stdout": observation.stdout_preview,
                "stderr": observation.stderr_preview,
                "kind": observation.kind,
                "message": observation.message,
            }
            if observation.kind == "timeout":
                # Recoverable partial output: the command was killed but produced
                # real output before that happened. is_error=False, with a trailing
                # notice appended so the model knows execution didn't complete
                # normally. Contrast with pure RPC/middleware tools that have no
                # partial business data on timeout (none exist yet in this tool
                # set) — those should stay is_error=True.
                content["timeout_notice"] = (
                    f"Command timed out after {observation.duration_ms}ms; "
                    "output above is whatever was produced before it was stopped."
                )
                is_error = False
            else:
                is_error = observation.kind not in {"command_result", "no_output"}
            return ToolResult(call.id, call.tool, 0, mode, content, is_error)
        return _error(call, 0, mode, "UNKNOWN_TOOL", f"Unknown tool: {call.tool}")


@contextmanager
def _workspace_write_lease(state: RunState):
    """Serialize write-capable tools across parent/background/child agents."""
    log = getattr(state, "_event_log", None)
    owner = f"{state.run_id}:{state.task_id or 'root'}"
    payload = {"workspace": str(state.workspace_host_path or Path.cwd()), "owner": owner, "operation": "write"}
    if log is not None:
        try:
            log.append("workspace/lock", {**payload, "status": "waiting"})
        except Exception:
            pass
    lease, epoch = WorkspaceLeaseRegistry.acquire(state.workspace_host_path, owner=owner)
    state.lease_epoch = epoch
    if log is not None:
        try:
            log.append("workspace/lock", {**payload, "status": "acquired", "epoch": epoch})
        except Exception:
            pass
    try:
        yield
    finally:
        lease.release()
        if log is not None:
            try:
                log.append("workspace/lock", {**payload, "status": "released", "epoch": epoch})
            except Exception:
                pass


class ToolCallScheduler:
    def __init__(self, runner: ToolRunner, *, max_parallel_tool_calls: int = 4) -> None:
        if max_parallel_tool_calls <= 0:
            raise ValueError("max_parallel_tool_calls must be a positive integer")
        self.runner = runner
        self.max_parallel_tool_calls = max_parallel_tool_calls

    def dispatch(self, calls: tuple[ToolCall, ...], state: RunState) -> list[ToolResult]:
        results: list[ToolResult] = []
        index = 0
        while index < len(calls):
            call = calls[index]
            mode = self._execution_mode(call)
            if mode == "exclusive":
                result = self._run_one(call, state)
                results.append(_with_order(result, index, mode))
                index += 1
                if state.status in {"waiting_approval", "failed", "interrupted"}:
                    for aborted_index in range(index, len(calls)):
                        aborted = calls[aborted_index]
                        results.append(
                            ToolResult(
                                aborted.id,
                                aborted.tool,
                                aborted_index,
                                self._execution_mode(aborted),
                                {
                                    "error_code": "ABORTED_BEFORE_DISPATCH",
                                    "error": "Call was not dispatched because an earlier call stopped the step.",
                                },
                                True,
                            )
                        )
                    break
                continue
            end = index
            while end < len(calls) and self._execution_mode(calls[end]) == "parallel":
                end += 1
            group = list(calls[index:end])
            results.extend(self._run_parallel_group(group, state, start_index=index))
            index = end
        return results

    def _run_parallel_group(
        self,
        group: list[ToolCall],
        state: RunState,
        *,
        start_index: int,
    ) -> list[ToolResult]:
        by_index: dict[int, ToolResult] = {}
        next_offset = 0
        futures: dict[Future[ToolResult], int] = {}
        with ThreadPoolExecutor(max_workers=self.max_parallel_tool_calls) as pool:
            while next_offset < len(group) and len(futures) < self.max_parallel_tool_calls:
                position = start_index + next_offset
                futures[pool.submit(self._run_one, group[next_offset], state)] = position
                next_offset += 1
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    position = futures.pop(future)
                    by_index[position] = _with_order(future.result(), position, "parallel")
                while (
                    state.status == "running"
                    and next_offset < len(group)
                    and len(futures) < self.max_parallel_tool_calls
                ):
                    position = start_index + next_offset
                    futures[pool.submit(self._run_one, group[next_offset], state)] = position
                    next_offset += 1
        while next_offset < len(group):
            call = group[next_offset]
            position = start_index + next_offset
            by_index[position] = ToolResult(
                call.id,
                call.tool,
                position,
                "parallel",
                {
                    "error_code": "ABORTED_BEFORE_DISPATCH",
                    "error": "Call was not dispatched because the step was aborted.",
                },
                True,
            )
            next_offset += 1
        return [by_index[position] for position in sorted(by_index)]

    def _execution_mode(self, call: ToolCall) -> ExecutionMode:
        try:
            classifier = getattr(self.runner, "execution_mode", None)
            classified = classifier(call) if callable(classifier) else None
        except Exception:
            return "exclusive"
        return "parallel" if classified == "parallel" else "exclusive"

    def _run_one(self, call: ToolCall, state: RunState) -> ToolResult:
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            result = self.runner.run(call, state)
        except Exception as exc:
            mode: ExecutionMode = "parallel" if call.tool in {"read", "delegate"} else "exclusive"
            result = _error(call, 0, mode, "TOOL_RUNTIME_ERROR", str(exc))
        completed_at = datetime.now(UTC)
        return ToolResult(
            result.call_id,
            result.tool,
            result.model_order,
            result.execution_mode,
            result.content,
            result.is_error,
            started_at.isoformat(),
            completed_at.isoformat(),
            int((time.perf_counter() - started) * 1000),
        )


def _with_order(result: ToolResult, order: int, mode: ExecutionMode) -> ToolResult:
    return ToolResult(
        result.call_id,
        result.tool,
        order,
        mode,
        result.content,
        result.is_error,
        result.started_at,
        result.completed_at,
        result.duration_ms,
    )


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ToolInputError("INVALID_ARGUMENT", f"{name} must be a positive integer")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
