from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AssertionResult:
    type: str
    passed: bool
    message: str


def run_assertions(
    assertions: list[dict[str, Any]],
    *,
    workspace_dir: Path,
    run_dir: Path,
    metrics: dict[str, Any] | None = None,
) -> list[AssertionResult]:
    return [
        run_assertion(assertion, workspace_dir=workspace_dir, run_dir=run_dir, metrics=metrics or {})
        for assertion in assertions
    ]


def run_assertion(
    assertion: dict[str, Any],
    *,
    workspace_dir: Path,
    run_dir: Path,
    metrics: dict[str, Any],
) -> AssertionResult:
    assertion_type = str(assertion.get("type") or "")
    if assertion_type == "command":
        return _assert_command(assertion, workspace_dir)
    if assertion_type == "file_exists":
        return _assert_file_exists(assertion, workspace_dir)
    if assertion_type == "file_not_exists":
        return _assert_file_not_exists(assertion, workspace_dir)
    if assertion_type == "file_contains":
        return _assert_file_contains(assertion, workspace_dir, should_contain=True)
    if assertion_type == "file_not_contains":
        return _assert_file_contains(assertion, workspace_dir, should_contain=False)
    if assertion_type == "metric_at_least":
        return _assert_metric_at_least(assertion, metrics)
    if assertion_type == "run_status":
        return _assert_run_status(assertion, metrics)
    if assertion_type == "trace_contains_event":
        return _assert_trace_contains_event(assertion, run_dir)
    if assertion_type == "no_policy_violation":
        return _assert_no_trace_event(run_dir, "policy_decision", decision_type="deny")
    if assertion_type == "diff_allowlist":
        return _assert_diff_allowlist(assertion, run_dir)
    if assertion_type == "diff_does_not_delete":
        return _assert_diff_does_not_delete(assertion, run_dir)
    if assertion_type == "no_source_diff":
        return _assert_no_source_diff(assertion, run_dir)
    if assertion_type == "max_changed_files":
        return _assert_max_changed_files(assertion, run_dir)
    return AssertionResult(assertion_type or "<missing>", False, f"Unsupported assertion type: {assertion_type}")


def _assert_command(assertion: dict[str, Any], workspace_dir: Path) -> AssertionResult:
    command = str(assertion.get("command") or "")
    expected = int(assertion.get("expect_exit_code", 0))
    completed = subprocess.run(
        _shell_args(command),
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=int(assertion.get("timeout_sec", 120)),
    )
    passed = completed.returncode == expected
    message = f"command exit_code={completed.returncode}, expected={expected}: {command}"
    if not passed and completed.stderr:
        message += f"\nstderr={completed.stderr[-1000:]}"
    return AssertionResult("command", passed, message)


def _assert_file_exists(assertion: dict[str, Any], workspace_dir: Path) -> AssertionResult:
    relative_path = str(assertion.get("path") or "")
    target = workspace_dir / relative_path
    return AssertionResult(
        "file_exists",
        target.exists(),
        f"file exists: {relative_path}",
    )


def _assert_file_not_exists(assertion: dict[str, Any], workspace_dir: Path) -> AssertionResult:
    relative_path = str(assertion.get("path") or "")
    target = workspace_dir / relative_path
    return AssertionResult(
        "file_not_exists",
        not target.exists(),
        f"file does not exist: {relative_path}",
    )


def _assert_file_contains(
    assertion: dict[str, Any],
    workspace_dir: Path,
    *,
    should_contain: bool,
) -> AssertionResult:
    relative_path = str(assertion.get("path") or "")
    target = workspace_dir / relative_path
    patterns = [str(pattern) for pattern in assertion.get("patterns", [])]
    if not target.exists():
        return AssertionResult(str(assertion.get("type")), False, f"file missing: {relative_path}")
    text = target.read_text(encoding="utf-8", errors="replace")
    missing = [pattern for pattern in patterns if pattern not in text]
    present = [pattern for pattern in patterns if pattern in text]
    if should_contain:
        return AssertionResult("file_contains", not missing, f"missing patterns in {relative_path}: {missing}")
    return AssertionResult("file_not_contains", not present, f"unexpected patterns in {relative_path}: {present}")


def _assert_metric_at_least(assertion: dict[str, Any], metrics: dict[str, Any]) -> AssertionResult:
    name = str(assertion.get("name") or "")
    expected = float(assertion.get("value", 0))
    actual = float(metrics.get(name, 0) or 0)
    return AssertionResult(
        "metric_at_least",
        actual >= expected,
        f"metric {name}={actual}, expected at least {expected}",
    )


def _assert_run_status(assertion: dict[str, Any], metrics: dict[str, Any]) -> AssertionResult:
    expected = str(assertion.get("value") or "")
    actual = str(metrics.get("status") or "")
    return AssertionResult(
        "run_status",
        actual == expected,
        f"run status={actual}, expected={expected}",
    )


def _assert_trace_contains_event(assertion: dict[str, Any], run_dir: Path) -> AssertionResult:
    event_type = str(assertion.get("event_type") or "")
    events = _read_trace(run_dir)
    passed = any(event.get("event") == event_type for event in events)
    return AssertionResult("trace_contains_event", passed, f"trace contains event: {event_type}")


def _assert_no_trace_event(run_dir: Path, event_type: str, **fields: str) -> AssertionResult:
    events = _read_trace(run_dir)
    matches = [
        event
        for event in events
        if event.get("event") == event_type and all(event.get(key) == value for key, value in fields.items())
    ]
    return AssertionResult("no_policy_violation", not matches, f"matching denied policy events: {len(matches)}")


def _assert_diff_allowlist(assertion: dict[str, Any], run_dir: Path) -> AssertionResult:
    allowed_paths = [str(path).replace("\\", "/").rstrip("/") + "/" for path in assertion.get("paths", [])]
    changed = _changed_files(run_dir)
    blocked = [path for path in changed if not any(path == item.rstrip("/") or path.startswith(item) for item in allowed_paths)]
    return AssertionResult("diff_allowlist", not blocked, f"changed files outside allowlist: {blocked}")


def _assert_no_source_diff(assertion: dict[str, Any], run_dir: Path) -> AssertionResult:
    protected = [str(path).replace("\\", "/").rstrip("/") + "/" for path in assertion.get("paths", [])]
    changed = _changed_files(run_dir)
    blocked = [path for path in changed if any(path == item.rstrip("/") or path.startswith(item) for item in protected)]
    return AssertionResult("no_source_diff", not blocked, f"protected files changed: {blocked}")


def _assert_diff_does_not_delete(assertion: dict[str, Any], run_dir: Path) -> AssertionResult:
    protected = [str(path).replace("\\", "/").rstrip("/") + "/" for path in assertion.get("paths", [])]
    deleted = _deleted_files(run_dir)
    blocked = [path for path in deleted if any(path == item.rstrip("/") or path.startswith(item) for item in protected)]
    return AssertionResult("diff_does_not_delete", not blocked, f"protected files deleted: {blocked}")


def _assert_max_changed_files(assertion: dict[str, Any], run_dir: Path) -> AssertionResult:
    limit = int(assertion.get("value", 0))
    changed = _changed_files(run_dir)
    return AssertionResult(
        "max_changed_files",
        len(changed) <= limit,
        f"changed_files={len(changed)}, limit={limit}",
    )


def _changed_files(run_dir: Path) -> list[str]:
    diff_path = run_dir / "artifacts" / "diff.patch"
    if not diff_path.exists():
        return []
    changed: list[str] = []
    for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("+++ b/"):
            path = line.removeprefix("+++ b/").strip()
            if path != "/dev/null":
                changed.append(path.replace("\\", "/"))
    return sorted(set(changed))


def _deleted_files(run_dir: Path) -> list[str]:
    diff_path = run_dir / "artifacts" / "diff.patch"
    if not diff_path.exists():
        return []
    deleted: list[str] = []
    for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("--- a/"):
            current = line.removeprefix("--- a/").strip()
            continue
        if line == "+++ /dev/null" and "current" in locals():
            deleted.append(current.replace("\\", "/"))
    return sorted(set(deleted))


def _read_trace(run_dir: Path) -> list[dict[str, Any]]:
    trace_path = run_dir / "trace.jsonl"
    if not trace_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _shell_args(command: str) -> list[str]:
    return ["bash", "-lc", _normalize_command_for_host_bash(command)]


def _normalize_command_for_host_bash(command: str) -> str:
    if sys.platform != "win32":
        return command
    return re.sub(r"(^|[;&|()\s])python(?=\s|$)", r"\1python3", command)
