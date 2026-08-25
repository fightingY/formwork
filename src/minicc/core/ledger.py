from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

LEDGER_SCHEMA_VERSION = 2
TERMINAL_RUN_STATUSES = {
    "completed",
    "failed",
    "waiting_approval",
    "interrupted",
    "orphaned",
}
FORMAL_METRIC_STATUSES = {"completed", "failed"}
REQUIRED_RUN_EVIDENCE = (
    "state.json",
    "trace.jsonl",
    "metrics.json",
    "workspace_manifest.json",
    "artifacts/diff.patch",
)


@dataclass(frozen=True)
class SuiteBundle:
    suite_id: str
    suite_dir: Path
    manifest_path: Path
    report_json_path: Path
    report_markdown_path: Path
    report_csv_path: Path


@dataclass(frozen=True)
class CleanupCandidate:
    run_id: str
    run_dir: Path
    reason: str


@dataclass(frozen=True)
class CleanupPlan:
    runs_root: Path
    protected_run_ids: tuple[str, ...]
    candidates: tuple[CleanupCandidate, ...]


@dataclass(frozen=True)
class CleanupResult:
    candidate_run_ids: list[str]
    deleted_run_ids: list[str]
    dry_run: bool


def new_suite_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"suite-{timestamp}-{uuid4().hex[:8]}"


def write_immutable_suite(
    suites_root: Path,
    *,
    suite_id: str,
    manifest: dict[str, Any],
    report: dict[str, Any],
    markdown: str,
    csv_text: str,
) -> SuiteBundle:
    suite_id = _safe_identifier(suite_id, kind="suite id")
    suites_root = suites_root.resolve()
    suite_dir = suites_root / suite_id
    if suite_dir.exists():
        raise FileExistsError(f"Suite evidence is immutable and already exists: {suite_dir}")
    temporary = suites_root / f".{suite_id}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        report_json_path = temporary / "report.json"
        report_markdown_path = temporary / "report.md"
        report_csv_path = temporary / "report.csv"
        _write_json(report_json_path, report)
        report_markdown_path.write_text(markdown, encoding="utf-8")
        report_csv_path.write_text(csv_text, encoding="utf-8")
        manifest_payload = dict(manifest)
        manifest_payload["artifacts"] = {
            "report_json": _artifact_entry(report_json_path),
            "report_markdown": _artifact_entry(report_markdown_path),
            "report_csv": _artifact_entry(report_csv_path),
        }
        _write_json(temporary / "manifest.json", manifest_payload)
        temporary.replace(suite_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return SuiteBundle(
        suite_id=suite_id,
        suite_dir=suite_dir,
        manifest_path=suite_dir / "manifest.json",
        report_json_path=suite_dir / "report.json",
        report_markdown_path=suite_dir / "report.md",
        report_csv_path=suite_dir / "report.csv",
    )


def _artifact_entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _indexed_artifact_entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def write_artifact_index(
    artifacts_root: Path,
    *,
    run_id: str,
    run_dir: Path,
    evidence: dict[str, str],
    hash_artifacts: bool = False,
) -> Path:
    run_id = _safe_identifier(run_id, kind="run id")
    target = artifacts_root.resolve() / run_id / "manifest.json"
    normalized_evidence = {
        name: str(Path(path).resolve())
        for name, path in evidence.items()
    }
    payload: dict[str, Any] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "artifact_index",
        "run_id": run_id,
        "run_dir": str(run_dir.resolve()),
        "evidence": normalized_evidence,
    }
    if hash_artifacts:
        payload["artifacts"] = {
            name: _indexed_artifact_entry(Path(path))
            for name, path in normalized_evidence.items()
            if name != "suite_manifest" and Path(path).is_file()
        }
    if target.exists():
        if _read_json(target) == payload:
            return target
        raise FileExistsError(f"Artifact index is immutable and already exists: {target}")
    _atomic_json(target, payload)
    return target


def run_evidence_complete(run_dir: Path, *, require_verifier: bool = True) -> bool:
    required = list(REQUIRED_RUN_EVIDENCE)
    if require_verifier:
        required.append("eval_result.json")
    return run_dir.is_dir() and all((run_dir / relative).is_file() for relative in required)


def inspect_run(
    run_dir: Path,
    *,
    now: datetime | None = None,
    orphan_after: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    state = _read_json(run_dir / "state.json")
    metrics = _read_json(run_dir / "metrics.json")
    eval_result = _read_json(run_dir / "eval_result.json")
    run_report = _read_json(run_dir / "run_report.json")
    result_record = eval_result or run_report
    schema_version = _schema_version(result_record or metrics or state)
    status = str(metrics.get("status") or state.get("status") or "unknown")
    status_source = "recorded"
    if status == "running" and _is_stale(state, metrics, run_dir, now, orphan_after):
        status = "orphaned"
        status_source = "recovery_scan"

    if schema_version >= LEDGER_SCHEMA_VERSION:
        task_success = _optional_bool(result_record.get("task_success"))
        agent_success = _optional_bool(result_record.get("agent_success"))
        infrastructure_success = _optional_bool(result_record.get("infrastructure_success"))
        policy_outcome = str(result_record.get("policy_outcome") or "unknown")
        semantics = "v2" if result_record else "v2/incomplete"
    else:
        task_success = None
        agent_success = None
        infrastructure_success = None
        policy_outcome = "unknown"
        semantics = "legacy/unknown"

    complete = run_evidence_complete(run_dir, require_verifier=bool(state.get("suite_id")))
    metric_terminal = status in FORMAL_METRIC_STATUSES or (
        status == "waiting_approval" and result_record.get("passed") is True
    )
    formal_metric_eligible = (
        schema_version >= LEDGER_SCHEMA_VERSION
        and complete
        and metric_terminal
        and task_success is not None
        and agent_success is not None
        and infrastructure_success is not None
        and policy_outcome != "unknown"
    )
    return {
        "schema_version": schema_version,
        "schema_semantics": semantics,
        "run_id": str(state.get("run_id") or run_dir.name),
        "suite_id": str(eval_result.get("suite_id") or state.get("suite_id") or ""),
        "milestone": str(eval_result.get("milestone") or state.get("milestone") or ""),
        "stage": str(eval_result.get("stage") or state.get("stage") or ""),
        "status": status,
        "status_source": status_source,
        "result": _result_from_status(status),
        "task_success": task_success,
        "agent_success": agent_success,
        "infrastructure_success": infrastructure_success,
        "policy_outcome": policy_outcome,
        "formal_metric_eligible": formal_metric_eligible,
        "evidence_complete": complete,
    }


def build_cleanup_plan(
    runs_root: Path,
    *,
    versions_root: Path | None = None,
    acceptance_root: Path | None = None,
    older_than: timedelta = timedelta(days=7),
    now: datetime | None = None,
) -> CleanupPlan:
    runs_root = runs_root.resolve()
    now = now or datetime.now(UTC)
    protected = set()
    if versions_root is not None:
        protected.update(_referenced_run_ids(versions_root, pattern="*/manifest.json"))
    suites_root = runs_root.parent / "suites"
    protected.update(_referenced_run_ids(suites_root, pattern="*/manifest.json"))
    if acceptance_root is not None:
        protected.update(_referenced_run_ids(acceptance_root, pattern="**/*.json"))

    candidates: list[CleanupCandidate] = []
    if runs_root.exists():
        cutoff = now.timestamp() - older_than.total_seconds()
        for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
            if run_dir.name == "eval_reports" or run_dir.name in protected:
                continue
            try:
                modified = run_dir.stat().st_mtime
            except OSError:
                continue
            if modified > cutoff:
                continue
            record = inspect_run(run_dir, now=now, orphan_after=older_than)
            candidates.append(
                CleanupCandidate(
                    run_id=run_dir.name,
                    run_dir=run_dir.resolve(),
                    reason=f"unreferenced {record['status']} run older than {older_than}",
                )
            )
    return CleanupPlan(
        runs_root=runs_root,
        protected_run_ids=tuple(sorted(protected)),
        candidates=tuple(candidates),
    )


def apply_cleanup_plan(plan: CleanupPlan, *, apply: bool = False) -> CleanupResult:
    candidate_ids = [candidate.run_id for candidate in plan.candidates]
    if not apply:
        return CleanupResult(candidate_ids, [], True)
    deleted: list[str] = []
    root = plan.runs_root.resolve()
    for candidate in plan.candidates:
        target = candidate.run_dir.resolve()
        if target.parent != root or target.name != candidate.run_id:
            raise ValueError(f"Cleanup candidate escapes runs root: {target}")
        if target.is_dir():
            shutil.rmtree(target, onerror=_retry_readonly_removal)
            deleted.append(candidate.run_id)
    return CleanupResult(candidate_ids, deleted, False)


def _retry_readonly_removal(
    operation: Callable[..., object],
    path: str,
    error_info: tuple[type[BaseException], BaseException, TracebackType | None],
) -> None:
    error = error_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    operation(path)


def _referenced_run_ids(root: Path, *, pattern: str) -> set[str]:
    referenced: set[str] = set()
    if not root.exists():
        return referenced
    for path in root.glob(pattern):
        payload = _read_json(path)
        referenced.update(_extract_run_ids(payload))
    return referenced


def _extract_run_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        run_id = value.get("run_id")
        if isinstance(run_id, str) and run_id:
            found.add(run_id)
        run_ids = value.get("run_ids")
        if isinstance(run_ids, list):
            found.update(str(item) for item in run_ids if item)
        for nested in value.values():
            found.update(_extract_run_ids(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_extract_run_ids(nested))
    return found


def _is_stale(
    state: dict[str, Any],
    metrics: dict[str, Any],
    run_dir: Path,
    now: datetime,
    orphan_after: timedelta,
) -> bool:
    raw_state_metrics = state.get("metrics")
    state_metrics = raw_state_metrics if isinstance(raw_state_metrics, dict) else {}
    started = metrics.get("started_at") or state_metrics.get("started_at")
    parsed = _parse_datetime(started)
    if parsed is None:
        try:
            parsed = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=UTC)
        except OSError:
            return False
    return now.astimezone(UTC) - parsed.astimezone(UTC) >= orphan_after


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _schema_version(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("schema_version", 1))
    except (TypeError, ValueError):
        return 1


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _result_from_status(status: str) -> str:
    return {
        "completed": "PASS",
        "failed": "FAIL",
        "waiting_approval": "WAITING",
        "interrupted": "INTERRUPTED",
        "orphaned": "UNKNOWN",
        "running": "RUNNING",
    }.get(status, "UNKNOWN")


def _safe_identifier(value: str, *, kind: str) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"Unsafe {kind}: {value}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{uuid4().hex[:8]}")
    _write_json(temporary, payload)
    os.replace(temporary, path)
