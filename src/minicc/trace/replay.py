"""Replay case packaging and trace-level regression checks.

The replay layer deliberately sits above the AgentLoop.  A case is an immutable
projection of a completed run: it contains the original trace, the baseline
workspace, and enough metadata to either validate the recorded trajectory
offline or drive the current CLI runtime against the same fixture.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from minicc.core.protocol import (
    PROTOCOL_SCHEMA_VERSION,
    ProtocolError,
    action_to_dict,
    parse_tool_call,
)

REPLAY_SCHEMA_VERSION = PROTOCOL_SCHEMA_VERSION


class ReplayError(ValueError):
    """Raised when a replay bundle is incomplete or unsafe to consume."""


@dataclass(frozen=True)
class ReplayResult:
    case_id: str
    mode: str
    passed: bool
    report_path: Path
    report: dict[str, Any]


def create_replay_case(
    run_dir: Path,
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Create an immutable replay bundle from one completed run."""
    source = run_dir.resolve()
    if not source.is_dir():
        raise ReplayError(f"Run directory does not exist: {source}")
    trace_path = source / "trace.jsonl"
    state_path = source / "state.json"
    if not trace_path.is_file() or not state_path.is_file():
        raise ReplayError("A replay source must contain both trace.jsonl and state.json.")

    state = _read_json(state_path)
    run_id = str(state.get("run_id") or source.name)
    case_id = f"replay-{run_id}"
    target_root = (output_dir or Path.cwd() / ".minicc" / "replays").resolve()
    target = target_root / case_id
    if target.exists():
        if not overwrite:
            raise ReplayError(f"Replay case already exists: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=False)

    copied: list[str] = []
    for relative in (
        "trace.jsonl",
        "state.json",
        "metrics.json",
        "run_report.json",
        "run_report.md",
        "workspace_manifest.json",
        "transcript.jsonl",
        "transcript.md",
        "repository_profile.json",
    ):
        source_file = source / relative
        if source_file.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied.append(relative)
    artifacts = source / "artifacts"
    if artifacts.is_dir():
        shutil.copytree(artifacts, target / "artifacts")
        copied.extend(f"artifacts/{path.relative_to(artifacts).as_posix()}" for path in artifacts.rglob("*"))

    workspace_snapshot = target / "workspace"
    workspace_mode = _snapshot_baseline(source / "workspace", workspace_snapshot)
    if workspace_mode == "missing":
        workspace_snapshot.mkdir(parents=True, exist_ok=True)

    events = read_trace(target / "trace.jsonl")
    response_rows = [
        {
            "sequence": event.get("sequence"),
            "response_text": event.get("response_text"),
            "latency_ms": event.get("latency_ms"),
            "usage": event.get("usage"),
        }
        for event in events
        if event.get("event") == "model_response"
    ]
    response_path = target / "model_responses.jsonl"
    response_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in response_rows),
        encoding="utf-8",
    )
    copied.append("model_responses.jsonl")

    manifest = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "case_id": case_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_run_id": run_id,
        "source_run_dir": str(source),
        "goal": str(state.get("goal") or ""),
        "action_defaults": {
            "timeout_sec": int((state.get("metrics") or {}).get("max_action_timeout_sec") or 60),
            "max_tool_calls": int((state.get("metrics") or {}).get("max_tool_calls_per_step") or 16),
        },
        "source_status": str(state.get("status") or "unknown"),
        "verification_commands": list((state.get("metrics") or {}).get("completion_verifier_commands") or []),
        "verification_timeout_sec": int((state.get("metrics") or {}).get("completion_verifier_timeout_sec") or 120),
        "workspace_mode": workspace_mode,
        "fresh_eligible": workspace_mode in {"git-baseline", "copy"},
        "deterministic_eligible": bool(response_rows)
        and all(row.get("response_text") is not None for row in response_rows),
        "event_count": len(events),
        "model_response_count": len(response_rows),
        "model_responses_complete": all(row.get("response_text") is not None for row in response_rows),
        "tool_result_count": sum(1 for event in events if event.get("event") in {"tool/result", "sandbox_exec_finished"}),
        "workspace": _directory_metadata(workspace_snapshot),
        "files": {},
    }
    for relative in copied:
        path = target / relative
        if path.is_file():
            manifest["files"][relative] = _file_metadata(path)
    (target / "case.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def create_replay_case_from_eval_case(
    case_path: Path,
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Create a fresh-replay seed directly from an ``eval`` case fixture."""
    from minicc.evals.case import load_case

    source = case_path.resolve()
    if source.is_dir():
        source = source / "case.yaml"
    eval_case = load_case(source)
    case_id = f"eval-{eval_case.name}"
    target = (output_dir or Path.cwd() / ".minicc" / "replays").resolve() / case_id
    if target.exists():
        if not overwrite:
            raise ReplayError(f"Replay case already exists: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=False)
    workspace = target / "workspace"
    shutil.copytree(eval_case.fixture_dir, workspace)
    (target / "case.yaml").write_bytes(source.read_bytes())
    verifier_dir = target / "verifier"
    verifier_dir.mkdir()
    assertions: list[dict[str, Any]] = []
    for raw_assertion in eval_case.assertions:
        assertion = dict(raw_assertion)
        raw_path = assertion.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            source_file = eval_case.case_dir / raw_path
            if source_file.is_file():
                destination = verifier_dir / Path(raw_path).name
                shutil.copy2(source_file, destination)
                assertion["path"] = destination.name
        assertions.append(assertion)
    manifest = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "case_id": case_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_kind": "eval_case",
        "source_eval_case": str(source),
        "goal": eval_case.prompt,
        "source_status": "not_run",
        "workspace_mode": "copy",
        "fresh_eligible": True,
        "deterministic_eligible": False,
        "event_count": 0,
        "model_response_count": 0,
        "model_responses_complete": False,
        "tool_result_count": 0,
        "verification_commands": [],
        "verification_timeout_sec": 120,
        "assertions": assertions,
        "files": {
            "case.yaml": _file_metadata(target / "case.yaml"),
        },
        "workspace": _directory_metadata(workspace),
    }
    for verifier_file in verifier_dir.rglob("*"):
        if verifier_file.is_file():
            relative = verifier_file.relative_to(target).as_posix()
            manifest["files"][relative] = _file_metadata(verifier_file)
    (target / "case.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def run_deterministic_replay(
    case_dir: Path,
    *,
    output_dir: Path | None = None,
) -> ReplayResult:
    """Validate a recorded trajectory without network or tool side effects."""
    case = case_dir.resolve()
    manifest = _load_case(case)
    if manifest.get("source_kind") == "eval_case" and not (case / "trace.jsonl").is_file():
        report = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "case_id": str(manifest.get("case_id") or case.name),
            "mode": "deterministic",
            "passed": False,
            "status": "not_available",
            "detail": "An eval-case seed has no recorded trace yet; run it first for deterministic replay.",
            "scorecard": {"overall": None, "checks_passed": 0, "checks_total": 0},
            "checks": {},
            "failures": ["eval-case seed has not been executed"],
        }
        destination = (output_dir or case).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        report_path = destination / "deterministic_replay_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (destination / "deterministic_replay_report.md").write_text(_markdown_report(report), encoding="utf-8")
        return ReplayResult(str(manifest.get("case_id") or case.name), "deterministic", False, report_path, report)
    events = read_trace(case / "trace.jsonl")
    checks: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    sequences = [event.get("sequence") for event in events if event.get("sequence") is not None]
    checks["event_order"] = _check(
        not sequences or sequences == sorted(sequences) == list(dict.fromkeys(sequences)),
        observed=len(events),
        detail="trace sequence is monotonic and unique",
    )
    if not checks["event_order"]["passed"]:
        failures.append("trace event sequence is not monotonic")

    starts = [event for event in events if event.get("event") == "run_started"]
    endings = [event for event in events if event.get("event") in {"run_completed", "run_failed", "run_interrupted"}]
    checks["lifecycle"] = _check(
        len(starts) >= 1 and len(endings) >= 1,
        observed={"run_started": len(starts), "terminal_events": len(endings)},
        detail="run has a start and terminal event",
    )
    if not checks["lifecycle"]["passed"]:
        failures.append("run lifecycle markers are incomplete")

    responses = [event for event in events if event.get("event") == "model_response"]
    complete_responses = [event for event in responses if isinstance(event.get("response_text"), str)]
    checks["model_response_fixtures"] = _check(
        bool(responses) and len(responses) == len(complete_responses),
        observed={"responses": len(responses), "complete": len(complete_responses)},
        detail="every model response has a full replay fixture",
    )
    if not checks["model_response_fixtures"]["passed"]:
        failures.append("one or more model responses are preview-only")

    parsed_actions = [event for event in events if event.get("event") == "action_parsed"]
    total_recorded_tool_calls = sum(len(event.get("tool_calls") or []) for event in responses)
    parse_coverage = len(parsed_actions) == total_recorded_tool_calls
    checks["action_parse_coverage"] = _check(
        parse_coverage,
        observed={"tool_calls": total_recorded_tool_calls, "action_parsed": len(parsed_actions)},
        detail="every recorded native tool_call has a recorded protocol parse result",
    )
    if not parse_coverage:
        failures.append("model response/action parse coverage is incomplete")

    protocol_replay_ok = True
    protocol_mismatches: list[dict[str, Any]] = []
    response_index = 0
    for event_index, event in enumerate(events):
        if event.get("event") != "model_response":
            continue
        response_index += 1
        recorded_tool_calls = event.get("tool_calls") or []
        defaults = manifest.get("action_defaults") or {}
        replayed_actions_accum: list[dict[str, Any]] = []
        replay_failed = False
        for tool_call in recorded_tool_calls:
            try:
                arguments = json.loads(tool_call.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("tool_call arguments must decode to a JSON object")
                replayed_actions_accum.append(
                    action_to_dict(
                        parse_tool_call(
                            str(tool_call.get("id") or ""),
                            str(tool_call.get("name") or ""),
                            arguments,
                            default_timeout_sec=int(defaults.get("timeout_sec") or 60),
                        )
                    )
                )
            except (TypeError, ValueError, ProtocolError):
                replay_failed = True
                break
        replayed_actions: list[dict[str, Any]] | None = None if replay_failed else replayed_actions_accum
        recorded_actions = [
            candidate.get("action")
            for candidate in events[event_index + 1 :]
            if candidate.get("event") == "action_parsed"
        ][: len(recorded_tool_calls)]
        if replayed_actions != recorded_actions:
            protocol_replay_ok = False
            protocol_mismatches.append(
                {
                    "response_index": response_index,
                    "replayed": replayed_actions,
                    "recorded": recorded_actions,
                }
            )
    checks["protocol_replay"] = _check(
        protocol_replay_ok,
        observed={"responses": len(responses), "mismatches": protocol_mismatches},
        detail="recorded native tool_calls parse to the recorded actions",
    )
    if not protocol_replay_ok:
        failures.append("recorded model response does not reproduce its parsed action")

    sandbox_started = [event for event in events if event.get("event") == "sandbox_exec_started"]
    sandbox_finished = [event for event in events if event.get("event") == "sandbox_exec_finished"]
    tool_calls = [event for event in events if event.get("event") == "tool/call"]
    tool_results = [event for event in events if event.get("event") == "tool/result"]
    tool_pairs_ok = _pair_call_results(tool_calls, tool_results)
    shell_pairs_ok = len(sandbox_started) == len(sandbox_finished)
    checks["tool_fixtures"] = _check(
        tool_pairs_ok and shell_pairs_ok,
        observed={
            "tool_calls": len(tool_calls),
            "tool_results": len(tool_results),
            "shell_starts": len(sandbox_started),
            "shell_finishes": len(sandbox_finished),
        },
        detail="tool and shell executions have complete result pairs",
    )
    if not checks["tool_fixtures"]["passed"]:
        failures.append("tool result coverage is incomplete")

    checks["artifact_manifest"] = _check(
        _hashes_match(case, manifest),
        observed=len(manifest.get("files", {})),
        detail="replay bundle file hashes are intact",
    )
    if not checks["artifact_manifest"]["passed"]:
        failures.append("replay bundle hash verification failed")

    passed = not failures
    report = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "case_id": str(manifest.get("case_id") or case.name),
        "mode": "deterministic",
        "passed": passed,
        "created_at": datetime.now(UTC).isoformat(),
        "source_run_id": manifest.get("source_run_id"),
        "scorecard": {
            "overall": 1.0 if passed else 0.0,
            "checks_passed": sum(1 for check in checks.values() if check["passed"]),
            "checks_total": len(checks),
            "event_count": len(events),
            "model_response_count": len(responses),
            "tool_result_count": len(tool_results) + len(sandbox_finished),
        },
        "checks": checks,
        "failures": failures,
    }
    destination = (output_dir or case).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "deterministic_replay_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "deterministic_replay_report.md").write_text(
        _markdown_report(report), encoding="utf-8"
    )
    return ReplayResult(str(manifest.get("case_id") or case.name), "deterministic", passed, report_path, report)


def compare_fresh_replay(
    case_dir: Path,
    fresh_run_dir: Path,
    *,
    output_dir: Path | None = None,
) -> ReplayResult:
    """Compare a fresh Runtime run with the source trajectory and workspace."""
    case = case_dir.resolve()
    fresh = fresh_run_dir.resolve()
    manifest = _load_case(case)
    if not fresh.is_dir():
        raise ReplayError(f"Fresh run directory does not exist: {fresh}")
    fresh_state = _read_json(fresh / "state.json")
    if manifest.get("source_kind") == "eval_case" and not (case / "trace.jsonl").is_file():
        from minicc.evals.assertions import run_assertions

        fresh_completed = fresh_state.get("status") == "completed"
        assertion_results = run_assertions(
            list(manifest.get("assertions") or []),
            workspace_dir=fresh / "workspace",
            run_dir=fresh,
            metrics=dict(fresh_state.get("metrics") or {}),
            verifier_dir=case / "verifier",
        )
        assertions_passed = all(result.passed for result in assertion_results)
        checks = {
            "fresh_run_completed": _check(
                fresh_completed,
                observed=fresh_state.get("status"),
                detail="fresh run completed",
            ),
            "eval_assertions": _check(
                assertions_passed,
                observed=[
                    {"type": result.type, "passed": result.passed, "message": result.message}
                    for result in assertion_results
                ],
                detail="eval case assertions pass on the fresh workspace",
            ),
        }
        report = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "case_id": str(manifest.get("case_id") or case.name),
            "mode": "fresh",
            "passed": fresh_completed and assertions_passed,
            "created_at": datetime.now(UTC).isoformat(),
            "source_kind": "eval_case",
            "fresh_run_id": fresh_state.get("run_id") or fresh.name,
            "scorecard": {
                "overall": sum(1 for check in checks.values() if check["passed"]) / len(checks),
                "checks_passed": sum(1 for check in checks.values() if check["passed"]),
                "checks_total": len(checks),
                "assertion_count": len(manifest.get("assertions") or []),
            },
            "checks": checks,
            "fresh_run_dir": str(fresh),
            "assertions": manifest.get("assertions") or [],
        }
        destination = (output_dir or case).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        report_path = destination / "fresh_replay_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (destination / "fresh_replay_report.md").write_text(_markdown_report(report), encoding="utf-8")
        return ReplayResult(str(manifest.get("case_id") or case.name), "fresh", fresh_completed, report_path, report)
    source_state = _read_json(case / "state.json")
    source_actions = _action_signatures(read_trace(case / "trace.jsonl"))
    fresh_actions = _action_signatures(read_trace(fresh / "trace.jsonl"))
    source_diff = _sha256(case / "artifacts" / "diff.patch")
    fresh_diff = _sha256(fresh / "artifacts" / "diff.patch")
    status_match = source_state.get("status") == fresh_state.get("status")
    action_match = source_actions == fresh_actions
    diff_match = source_diff == fresh_diff if source_diff and fresh_diff else None
    fresh_completed = fresh_state.get("status") == "completed"
    checks = {
        "fresh_run_completed": _check(fresh_completed, observed=fresh_state.get("status"), detail="fresh run completed"),
        "status_match": _check(status_match, observed={"source": source_state.get("status"), "fresh": fresh_state.get("status")}, detail="terminal status matches"),
        "action_sequence_match": _check(action_match, observed={"source": len(source_actions), "fresh": len(fresh_actions)}, detail="action sequence matches exactly"),
        "diff_match": _check(diff_match is True, observed={"source": source_diff, "fresh": fresh_diff}, detail="workspace diff hash matches") if diff_match is not None else {"passed": False, "observed": None, "detail": "diff is unavailable"},
    }
    report = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "case_id": str(manifest.get("case_id") or case.name),
        "mode": "fresh",
        "passed": all(check["passed"] for check in checks.values()),
        "created_at": datetime.now(UTC).isoformat(),
        "source_run_id": manifest.get("source_run_id"),
        "fresh_run_id": fresh_state.get("run_id") or fresh.name,
        "scorecard": {
            "overall": sum(1 for check in checks.values() if check["passed"]) / len(checks),
            "checks_passed": sum(1 for check in checks.values() if check["passed"]),
            "checks_total": len(checks),
            "source_actions": len(source_actions),
            "fresh_actions": len(fresh_actions),
            "source_event_count": len(read_trace(case / "trace.jsonl")),
            "fresh_event_count": len(read_trace(fresh / "trace.jsonl")),
        },
        "checks": checks,
        "fresh_run_dir": str(fresh),
    }
    destination = (output_dir or case).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "fresh_replay_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "fresh_replay_report.md").write_text(_markdown_report(report), encoding="utf-8")
    return ReplayResult(str(manifest.get("case_id") or case.name), "fresh", bool(report["passed"]), report_path, report)


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ReplayError(f"Trace file does not exist: {path}")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"Invalid trace JSON at line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ReplayError(f"Trace event at line {line_number} is not an object.")
        events.append(value)
    return events


def _snapshot_baseline(source: Path, destination: Path) -> str:
    if not source.is_dir():
        return "missing"
    manifest_path = source.parent / "workspace_manifest.json"
    baseline_ref = "refs/minicc/baseline"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        candidate = ((manifest.get("included") or {}).get("baseline_commit"))
        if candidate:
            baseline_ref = str(candidate)
    try:
        process = subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", baseline_ref],
            capture_output=True,
            check=True,
            timeout=60,
        )
        destination.mkdir(parents=True, exist_ok=True)
        _safe_extract_tar(process.stdout, destination)
        return "git-baseline"
    except (OSError, subprocess.SubprocessError, tarfile.TarError):
        shutil.copytree(source, destination)
        return "copy"


def _safe_extract_tar(payload: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        root = destination.resolve()
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise ReplayError(f"Unsafe path in workspace snapshot: {member.name}")
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                if link_target != root and root not in link_target.parents:
                    raise ReplayError(f"Unsafe link in workspace snapshot: {member.name}")
        archive.extractall(destination)


def _load_case(case: Path) -> dict[str, Any]:
    manifest_path = case / "case.json"
    if not manifest_path.is_file():
        raise ReplayError(f"Replay case manifest does not exist: {manifest_path}")
    manifest = _read_json(manifest_path)
    if int(manifest.get("schema_version", 0)) != REPLAY_SCHEMA_VERSION:
        raise ReplayError("Unsupported replay case schema version.")
    return manifest


def _pair_call_results(calls: list[dict[str, Any]], results: list[dict[str, Any]]) -> bool:
    if not calls and not results:
        return True
    result_ids = {str(event.get("call_id")) for event in results}
    return all(str(event.get("call_id")) in result_ids for event in calls) and len(calls) == len(results)


def _action_signatures(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signatures: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") == "action_started":
            action = event.get("action")
            if isinstance(action, dict):
                signatures.append({"type": action.get("type"), "action": action})
        elif event.get("event") == "tool/call":
            signatures.append({"type": "tool_call", "tool": event.get("tool"), "arguments": event.get("arguments")})
    return signatures


def _hashes_match(case: Path, manifest: dict[str, Any]) -> bool:
    workspace_metadata = manifest.get("workspace")
    if isinstance(workspace_metadata, dict) and _directory_metadata(case / "workspace") != workspace_metadata:
        return False
    for relative, metadata in (manifest.get("files") or {}).items():
        path = case / str(relative)
        if not path.is_file() or _file_metadata(path) != metadata:
            return False
    return True


def _directory_metadata(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    files = 0
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.relative_to(root).parts:
                continue
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(data).digest())
            files += 1
    return {"file_count": files, "sha256": digest.hexdigest()}


def _file_metadata(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReplayError(f"Expected JSON object: {path}")
    return value


def _check(passed: bool, *, observed: Any, detail: str) -> dict[str, Any]:
    return {"passed": bool(passed), "observed": observed, "detail": detail}


def _markdown_report(report: dict[str, Any]) -> str:
    overall = (report.get("scorecard") or {}).get("overall")
    score_text = "n/a" if overall is None else f"{float(overall):.2f}"
    lines = [
        f"# {report.get('mode', 'replay').title()} Replay Report",
        "",
        f"- Case: `{report.get('case_id', '')}`",
        f"- Passed: `{report.get('passed')}`",
        f"- Score: `{score_text}`",
        "",
        "## Checks",
        "",
        "| Check | Passed | Detail |",
        "|---|---:|---|",
    ]
    for name, check in (report.get("checks") or {}).items():
        lines.append(f"| `{name}` | `{check.get('passed')}` | {check.get('detail', '')} |")
    failures = report.get("failures") or []
    if failures:
        lines.extend(["", "## Failures", "", *[f"- {item}" for item in failures]])
    return "\n".join(lines) + "\n"
