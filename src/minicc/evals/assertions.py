from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AssertionResult:
    type: str
    passed: bool
    message: str
    spec_sha256: str | None = None


def run_assertions(
    assertions: list[dict[str, Any]],
    *,
    workspace_dir: Path,
    run_dir: Path,
    metrics: dict[str, Any] | None = None,
    verifier_dir: Path | None = None,
) -> list[AssertionResult]:
    return [
        run_assertion(
            assertion,
            workspace_dir=workspace_dir,
            run_dir=run_dir,
            metrics=metrics or {},
            verifier_dir=verifier_dir,
        )
        for assertion in assertions
    ]


def run_assertion(
    assertion: dict[str, Any],
    *,
    workspace_dir: Path,
    run_dir: Path,
    metrics: dict[str, Any],
    verifier_dir: Path | None = None,
) -> AssertionResult:
    assertion_type = str(assertion.get("type") or "")
    if assertion_type == "command":
        return _assert_command(assertion, workspace_dir, run_dir)
    if assertion_type == "python_verifier":
        return _assert_python_verifier(
            assertion,
            workspace_dir=workspace_dir,
            verifier_dir=verifier_dir,
        )
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
    if assertion_type == "metric_equals":
        return _assert_metric_equals(assertion, metrics)
    if assertion_type == "run_status":
        return _assert_run_status(assertion, metrics)
    if assertion_type == "trace_contains_event":
        return _assert_trace_contains_event(assertion, run_dir)
    if assertion_type == "trace_action_sequence":
        return _assert_trace_action_sequence(assertion, run_dir)
    if assertion_type == "trace_action_shape":
        return _assert_trace_action_shape(assertion, run_dir)
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


def _assert_command(
    assertion: dict[str, Any],
    workspace_dir: Path,
    run_dir: Path | None = None,
) -> AssertionResult:
    command = str(assertion.get("command") or "")
    expected = int(assertion.get("expect_exit_code", 0))
    try:
        completed = subprocess.run(
            _shell_args(command),
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(assertion.get("timeout_sec", 120)),
        )
        stdout = getattr(completed, "stdout", "") or ""
        stderr = getattr(completed, "stderr", "") or ""
        exit_code = getattr(completed, "returncode", None)
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout)
        stderr = _decode_output(exc.stderr)
        exit_code = None
    except OSError as exc:
        stdout = ""
        stderr = str(exc)
        exit_code = None
    artifact_label = re.sub(r"[^a-z0-9_-]+", "-", str(assertion.get("_artifact_label") or "final").lower())
    if run_dir is not None:
        artifact = (
            run_dir
            / "artifacts"
            / "verification"
            / f"{artifact_label}-{hashlib.sha256(command.encode('utf-8')).hexdigest()[:12]}.json"
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps(
                {"command": command, "expected_exit_code": expected, "exit_code": exit_code, "stdout": stdout, "stderr": stderr},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    passed = exit_code == expected
    message = f"command exit_code={exit_code}, expected={expected}: {command}"
    if not passed and stderr:
        message += f"\nstderr={stderr[-1000:]}"
    return AssertionResult("command", passed, message)


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _assert_python_verifier(
    assertion: dict[str, Any],
    *,
    workspace_dir: Path,
    verifier_dir: Path | None,
) -> AssertionResult:
    relative_path = str(assertion.get("path") or "").replace("\\", "/").strip()
    expected_sha256 = str(assertion.get("sha256") or "").lower()
    if verifier_dir is None:
        return AssertionResult(
            "python_verifier",
            False,
            "python verifier directory is not configured",
        )
    path = Path(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or ".." in path.parts
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        return AssertionResult(
            "python_verifier",
            False,
            "python verifier requires a safe relative path and a SHA-256 digest",
        )

    verifier_root = verifier_dir.resolve()
    script = (verifier_root / path).resolve()
    if verifier_root not in script.parents or not script.is_file():
        return AssertionResult(
            "python_verifier",
            False,
            f"python verifier is missing or escapes its root: {relative_path}",
        )
    actual_sha256 = hashlib.sha256(script.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        return AssertionResult(
            "python_verifier",
            False,
            (
                f"python verifier digest mismatch: expected={expected_sha256}, "
                f"actual={actual_sha256}"
            ),
            expected_sha256,
        )

    env = os.environ.copy()
    env["MINICC_WORKSPACE"] = str(workspace_dir.resolve())
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=verifier_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(assertion.get("timeout_sec", 120)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AssertionResult(
            "python_verifier",
            False,
            f"python verifier could not complete: {exc}",
            expected_sha256,
        )

    passed = completed.returncode == int(assertion.get("expect_exit_code", 0))
    message = (
        f"python verifier exit_code={completed.returncode}: {relative_path}; "
        f"sha256={expected_sha256}"
    )
    if not passed:
        output = (completed.stdout + completed.stderr).strip()
        if output:
            message += f"\noutput={output[-2000:]}"
    return AssertionResult(
        "python_verifier",
        passed,
        message,
        expected_sha256,
    )


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


def _assert_metric_equals(assertion: dict[str, Any], metrics: dict[str, Any]) -> AssertionResult:
    name = str(assertion.get("name") or "")
    expected = float(assertion.get("value", 0))
    actual = float(metrics.get(name, 0) or 0)
    present = name in metrics
    return AssertionResult(
        "metric_equals",
        present and actual == expected,
        (
            f"metric {name}={actual}, expected exactly {expected}; "
            f"field_present={present}"
        ),
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
    raw_fields = assertion.get("fields", {})
    expected_fields = {str(key): value for key, value in raw_fields.items()} if isinstance(raw_fields, dict) else {}
    events = _read_trace(run_dir)
    passed = any(
        event.get("event") == event_type
        and all(event.get(key) == value for key, value in expected_fields.items())
        for event in events
    )
    fields_message = f" with fields {expected_fields}" if expected_fields else ""
    return AssertionResult("trace_contains_event", passed, f"trace contains event: {event_type}{fields_message}")


def _assert_trace_action_sequence(
    assertion: dict[str, Any],
    run_dir: Path,
) -> AssertionResult:
    raw_commands = assertion.get("commands")
    if (
        not isinstance(raw_commands, list)
        or not raw_commands
        or not all(isinstance(command, str) and command.strip() for command in raw_commands)
    ):
        return AssertionResult(
            "trace_action_sequence",
            False,
            "trace action sequence requires a non-empty list of exact commands",
        )

    expected = [_normalize_trace_command(command) for command in raw_commands]
    actual = [
        _normalize_trace_command(str(action.get("command") or ""))
        for event in _read_trace(run_dir)
        if event.get("event") == "action_parsed"
        and isinstance((action := event.get("action")), dict)
        and action.get("type") == "bash"
    ]
    sequence_length = len(expected)
    raw_start_index = assertion.get("start_index")
    if raw_start_index is None:
        passed = any(
            actual[index : index + sequence_length] == expected
            for index in range(len(actual) - sequence_length + 1)
        )
        position_message = "at any position"
    else:
        try:
            start_index = int(raw_start_index)
        except (TypeError, ValueError):
            start_index = 0
        passed = (
            start_index >= 1
            and actual[start_index - 1 : start_index - 1 + sequence_length]
            == expected
        )
        position_message = f"at 1-based bash index {raw_start_index}"
    return AssertionResult(
        "trace_action_sequence",
        passed,
        (
            "trace contains contiguous exact bash command sequence "
            f"{position_message}: {expected}; "
            f"observed bash commands: {actual}"
        ),
    )


def _assert_trace_action_shape(
    assertion: dict[str, Any],
    run_dir: Path,
) -> AssertionResult:
    return assert_trace_action_shape_events(
        assertion,
        _read_trace(run_dir),
    )


def assert_trace_action_shape_events(
    assertion: dict[str, Any],
    events: list[dict[str, Any]],
) -> AssertionResult:
    raw_actions = assertion.get("actions")
    if (
        not isinstance(raw_actions, list)
        or not raw_actions
        or not all(isinstance(action, dict) for action in raw_actions)
    ):
        return AssertionResult(
            "trace_action_shape",
            False,
            "trace action shape requires a non-empty list of action specs",
        )

    specs = [dict(action) for action in raw_actions]
    valid_specs = all(_valid_action_shape_spec(spec) for spec in specs)
    bash_events = [
        (index, str(action.get("command") or "").strip())
        for index, event in enumerate(events)
        if event.get("event") == "action_parsed"
        and isinstance((action := event.get("action")), dict)
        and action.get("type") == "bash"
    ]
    actual = [command for _, command in bash_events]
    matched = valid_specs and len(actual) == len(specs)
    if matched:
        for action_index, ((event_index, command), spec) in enumerate(
            zip(bash_events, specs, strict=True)
        ):
            exact = spec.get("command")
            pattern = spec.get("command_regex")
            heredoc_write = spec.get("heredoc_write")
            if isinstance(exact, str):
                if command != exact.strip():
                    matched = False
                    break
            elif isinstance(pattern, str):
                if re.fullmatch(pattern, command) is None:
                    matched = False
                    break
            elif not isinstance(heredoc_write, dict) or not _matches_heredoc_write(
                command,
                path=str(heredoc_write["path"]),
                delimiter=str(heredoc_write["delimiter"]),
            ):
                matched = False
                break
            next_event_index = (
                bash_events[action_index + 1][0]
                if action_index + 1 < len(bash_events)
                else len(events)
            )
            finished = [
                event
                for event in events[event_index + 1 : next_event_index]
                if event.get("event") == "sandbox_exec_finished"
            ]
            if len(finished) != 1:
                matched = False
                break
            observation = finished[0].get("observation")
            if not isinstance(observation, dict):
                matched = False
                break
            expected_exit_code = spec.get("expect_exit_code")
            if (
                expected_exit_code is not None
                and observation.get("exit_code") != expected_exit_code
            ):
                matched = False
                break
            if "artifact_ids" in spec:
                expected_artifacts = spec["artifact_ids"]
                actual_artifacts = observation.get("artifact_ids")
                if actual_artifacts != expected_artifacts:
                    matched = False
                    break
                written_artifacts = [
                    event.get("artifact_id")
                    for event in events[event_index + 1 : next_event_index]
                    if event.get("event") == "artifact_written"
                ]
                if any(
                    artifact_id not in written_artifacts
                    for artifact_id in expected_artifacts
                ):
                    matched = False
                    break

    spec = {"type": "trace_action_shape", "actions": specs}
    spec_sha256 = assertion_spec_sha256(spec)
    return AssertionResult(
        "trace_action_shape",
        matched,
        (
            f"trace bash action shape matches locked spec {spec_sha256}; "
            f"observed bash commands: {actual}"
        ),
        spec_sha256,
    )


def trace_action_shape_evidence_events(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only the ordered trace fields consumed by action-shape assertions."""
    normalized: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event")
        if event_type == "action_parsed":
            action = event.get("action")
            if not isinstance(action, Mapping) or action.get("type") != "bash":
                continue
            normalized.append(
                {
                    "event": "action_parsed",
                    "action": {
                        "type": "bash",
                        "command": action.get("command"),
                    },
                }
            )
        elif event_type == "sandbox_exec_finished":
            observation = event.get("observation")
            normalized.append(
                {
                    "event": "sandbox_exec_finished",
                    "observation": (
                        {
                            "exit_code": observation.get("exit_code"),
                            "artifact_ids": observation.get("artifact_ids"),
                        }
                        if isinstance(observation, Mapping)
                        else None
                    ),
                }
            )
        elif event_type == "artifact_written":
            normalized.append(
                {
                    "event": "artifact_written",
                    "artifact_id": event.get("artifact_id"),
                }
            )
    return normalized


def _valid_action_shape_spec(spec: dict[str, Any]) -> bool:
    matcher_keys = {
        key
        for key in ("command", "command_regex", "heredoc_write")
        if key in spec
    }
    if len(matcher_keys) != 1:
        return False
    if set(spec) - matcher_keys - {"expect_exit_code", "artifact_ids"}:
        return False
    if "command" in spec and not isinstance(spec["command"], str):
        return False
    if "command_regex" in spec and not isinstance(spec["command_regex"], str):
        return False
    if "heredoc_write" in spec:
        heredoc = spec["heredoc_write"]
        if (
            not isinstance(heredoc, dict)
            or set(heredoc) != {"path", "delimiter"}
            or not all(
                isinstance(value, str) and bool(value)
                for value in heredoc.values()
            )
        ):
            return False
    if "expect_exit_code" in spec and not isinstance(
        spec["expect_exit_code"],
        int,
    ):
        return False
    if "artifact_ids" in spec and (
        not isinstance(spec["artifact_ids"], list)
        or not all(
            isinstance(artifact_id, str) and bool(artifact_id)
            for artifact_id in spec["artifact_ids"]
        )
    ):
        return False
    return True


def _matches_heredoc_write(
    command: str,
    *,
    path: str,
    delimiter: str,
) -> bool:
    lines = command.splitlines()
    if len(lines) < 3:
        return False
    if lines[0] != f"cat > {path} << '{delimiter}'":
        return False
    if lines[-1] != delimiter:
        return False
    body = lines[1:-1]
    return bool(body) and delimiter not in body


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


def _normalize_trace_command(command: str) -> str:
    return command.strip()


def assertion_spec_sha256(assertion: dict[str, Any]) -> str:
    payload = json.dumps(
        assertion,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _shell_args(command: str) -> list[str]:
    if sys.platform == "win32" and _uses_windows_native_build_tool(command):
        return ["cmd.exe", "/d", "/s", "/c", command]
    if sys.platform == "win32" and _uses_simple_python_command(command):
        parts = shlex.split(command, posix=True)
        return [sys.executable, *parts[1:]]
    return ["bash", "-lc", _normalize_command_for_host_bash(command)]


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


def _normalize_command_for_host_bash(command: str) -> str:
    if sys.platform != "win32":
        return command
    normalized = re.sub(r"(^|[;&|()\s])python(?=\s|$)", r"\1python3", command)
    return re.sub(r"(?<![\w./-])mvn(?=\s)", "cmd.exe /c mvn", normalized)
