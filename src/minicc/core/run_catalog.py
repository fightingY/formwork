from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minicc.core.ledger import LEDGER_SCHEMA_VERSION, run_evidence_complete
from minicc.core.state import RunState


SCHEMA_VERSION = LEDGER_SCHEMA_VERSION
STAGE_LABELS = {
    "formal_acceptance": "正式验收",
    "development_precheck": "开发预检",
    "failure_reproduction": "失败复现",
    "post_fix_rerun": "修复后重跑",
    "regression": "回归验证",
    "checkpoint_resume": "Checkpoint恢复",
    "daily_development": "日常开发",
}


class RunCatalog:
    """Maintain lightweight, versioned pointers to immutable run directories."""

    def __init__(self, versions_root: Path) -> None:
        self.versions_root = versions_root

    def ensure_version(self, milestone: str) -> Path | None:
        milestone = _safe_milestone(milestone)
        if not milestone:
            return None
        manifest_path = self._manifest_path(milestone)
        if not manifest_path.exists():
            self._write_manifest(milestone, [])
        return manifest_path

    def register_state(
        self,
        milestone: str,
        state: RunState,
        *,
        stage: str = "daily_development",
        source: str = "run",
        git_commit: str = "",
    ) -> dict[str, Any] | None:
        if state.run_dir is None:
            return None
        result = _result_from_status(state.status)
        evidence_valid = run_evidence_complete(state.run_dir, require_verifier=False)
        return self.upsert(
            milestone,
            {
                "run_id": state.run_id,
                "schema_version": LEDGER_SCHEMA_VERSION,
                "entity_type": "run",
                "run_dir": str(state.run_dir.resolve()),
                "suite_id": state.suite_id or "",
                "stage": stage,
                "source": source,
                "status": state.status,
                "result": result,
                "goal": state.goal,
                "started_at": state.metrics.get("started_at") or _timestamp_from_run_id(state.run_id),
                "completed_at": state.metrics.get("completed_at"),
                "git_commit": git_commit,
                "evidence_valid": evidence_valid,
                "formal_metric_eligible": False,
            },
        )

    def register_eval_result(
        self,
        milestone: str,
        result: Any,
        *,
        stage: str,
        source: str = "eval",
        git_commit: str = "",
        report_path: str = "",
        suite_path: str = "",
    ) -> dict[str, Any] | None:
        metrics = dict(getattr(result, "metrics", {}) or {})
        run_dir = Path(result.run_dir).resolve()
        resolved_suite_path = Path(suite_path).resolve() if suite_path else None
        if not run_evidence_complete(run_dir, require_verifier=True):
            return None
        if resolved_suite_path is None or not resolved_suite_path.is_file():
            return None
        suite_manifest = _read_json(resolved_suite_path)
        eval_record = _read_json(run_dir / "eval_result.json")
        suite_id = str(getattr(result, "suite_id", "") or "")
        if (
            _schema_version(suite_manifest, default=0) < LEDGER_SCHEMA_VERSION
            or str(suite_manifest.get("suite_id") or "") != suite_id
            or _schema_version(eval_record, default=0) < LEDGER_SCHEMA_VERSION
            or str(eval_record.get("run_id") or "") != str(result.run_id)
            or str(eval_record.get("suite_id") or "") != suite_id
        ):
            return None
        task_success = getattr(result, "task_success", None)
        agent_success = getattr(result, "agent_success", None)
        infrastructure_success = getattr(result, "infrastructure_success", None)
        policy_outcome = str(getattr(result, "policy_outcome", "unknown") or "unknown")
        formal_metric_eligible = (
            str(stage) == "formal_acceptance"
            and str(result.run_status) in {"completed", "failed"}
            and all(isinstance(value, bool) for value in (task_success, agent_success, infrastructure_success))
            and policy_outcome != "unknown"
        )
        return self.upsert(
            milestone,
            {
                "run_id": str(result.run_id),
                "schema_version": LEDGER_SCHEMA_VERSION,
                "entity_type": "run",
                "run_dir": str(run_dir),
                "suite_id": suite_id,
                "suite_path": str(resolved_suite_path),
                "stage": stage,
                "source": source,
                "status": str(result.run_status),
                "result": "PASS" if bool(result.passed) else "FAIL",
                "case_name": str(result.name),
                "attempt": int(result.attempt),
                "goal": "",
                "started_at": metrics.get("started_at") or _timestamp_from_run_id(str(result.run_id)),
                "completed_at": metrics.get("completed_at"),
                "git_commit": git_commit,
                "report_path": report_path,
                "task_success": task_success,
                "agent_success": agent_success,
                "infrastructure_success": infrastructure_success,
                "policy_outcome": policy_outcome,
                "evidence_valid": True,
                "formal_metric_eligible": formal_metric_eligible,
            },
        )

    def upsert(self, milestone: str, raw_entry: dict[str, Any]) -> dict[str, Any] | None:
        milestone = _safe_milestone(milestone)
        run_id = str(raw_entry.get("run_id") or "").strip()
        if not milestone or not run_id:
            return None

        manifest = self.read_manifest(milestone)
        entries = list(manifest.get("entries", []))
        existing = next((item for item in entries if item.get("run_id") == run_id), None)
        merged = dict(existing or {})
        merged.update({key: value for key, value in raw_entry.items() if value is not None and value != ""})
        merged["run_id"] = run_id
        merged["milestone"] = milestone
        merged["stage"] = str(merged.get("stage") or "daily_development")
        merged["stage_label"] = STAGE_LABELS.get(merged["stage"], merged["stage"])
        merged["title"] = _entry_title(merged)
        merged["updated_at"] = _now()

        old_entry_file = str((existing or {}).get("entry_file") or "")
        entry_file = self._entry_relative_path(milestone, merged)
        merged["entry_file"] = entry_file.as_posix()
        if old_entry_file and old_entry_file != merged["entry_file"]:
            old_path = self._version_dir(milestone) / old_entry_file
            if old_path.is_file():
                old_path.unlink()

        entries = [item for item in entries if item.get("run_id") != run_id]
        entries.append(merged)
        entries.sort(key=_entry_sort_key, reverse=True)
        self._write_manifest(milestone, entries)
        self._write_entry_file(milestone, merged)
        return merged

    def update_existing_state(self, state: RunState, *, fallback_milestone: str = "") -> dict[str, Any] | None:
        for milestone in self.milestones():
            manifest = self.read_manifest(milestone)
            if any(item.get("run_id") == state.run_id for item in manifest.get("entries", [])):
                if state.run_dir is None:
                    return None
                return self.upsert(
                    milestone,
                    {
                        "run_id": state.run_id,
                        "run_dir": str(state.run_dir.resolve()),
                        "status": state.status,
                        "result": _result_from_status(state.status),
                        "goal": state.goal,
                        "started_at": state.metrics.get("started_at") or _timestamp_from_run_id(state.run_id),
                        "completed_at": state.metrics.get("completed_at"),
                    },
                )
        return self.register_state(fallback_milestone, state)

    def read_manifest(self, milestone: str) -> dict[str, Any]:
        milestone = _safe_milestone(milestone)
        path = self._manifest_path(milestone)
        if not path.exists():
            return {
                "schema_version": SCHEMA_VERSION,
                "milestone": milestone,
                "updated_at": "",
                "entries": [],
            }
        data = _read_json(path)
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            entries = []
        return {
            "schema_version": _schema_version(data, default=1),
            "milestone": milestone,
            "updated_at": str(data.get("updated_at") or ""),
            "entries": [item for item in entries if isinstance(item, dict)],
        }

    def milestones(self) -> list[str]:
        if not self.versions_root.exists():
            return []
        return sorted(
            path.name
            for path in self.versions_root.iterdir()
            if path.is_dir() and (path / "manifest.json").exists()
        )

    def _write_manifest(self, milestone: str, entries: list[dict[str, Any]]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "milestone": milestone,
            "updated_at": _now(),
            "entry_count": len(entries),
            "entries": entries,
        }
        _atomic_write(self._manifest_path(milestone), payload)

    def _write_entry_file(self, milestone: str, entry: dict[str, Any]) -> None:
        target = self._version_dir(milestone) / str(entry["entry_file"])
        _atomic_write(target, entry)

    def _entry_relative_path(self, milestone: str, entry: dict[str, Any]) -> Path:
        stage_label = _safe_component(str(entry["stage_label"]))
        title = _safe_component(str(entry["title"]))
        run_id = _safe_component(str(entry["run_id"]))
        return Path(stage_label) / f"{title}--{run_id}.json"

    def _version_dir(self, milestone: str) -> Path:
        return self.versions_root / milestone

    def _manifest_path(self, milestone: str) -> Path:
        return self._version_dir(milestone) / "manifest.json"


def index_acceptance_history(project_root: Path, catalog: RunCatalog | None = None) -> dict[str, int]:
    """Rebuild the local V1.3/V2.0 index from the checked-in acceptance evidence."""

    project_root = project_root.resolve()
    runs_root = project_root / ".minicc" / "runs"
    catalog = catalog or RunCatalog(project_root / ".minicc" / "versions")
    counts: dict[str, int] = {}

    report_rules = [
        (project_root / "acceptance" / "stable-v1.3" / "eval_report.json", "stable-v1.3", "formal_acceptance"),
        (
            project_root / "acceptance" / "archive" / "stable-v1.3-development" / "precheck-1" / "eval_report.json",
            "stable-v1.3",
            "development_precheck",
        ),
        (
            project_root / "acceptance" / "archive" / "stable-v1.3-development" / "precheck-2" / "eval_report.json",
            "stable-v1.3",
            "development_precheck",
        ),
        (
            project_root / "acceptance" / "archive" / "stable-v1.3-development" / "precheck-3" / "eval_report.json",
            "stable-v1.3",
            "development_precheck",
        ),
        (
            project_root / "acceptance" / "archive" / "stable-v1.3-development" / "full-matrix-1" / "eval_report.json",
            "stable-v1.3",
            "failure_reproduction",
        ),
        (
            project_root / "acceptance" / "archive" / "stable-v1.3-development" / "c01-c02-rerun" / "eval_report.json",
            "stable-v1.3",
            "post_fix_rerun",
        ),
        (
            project_root / "acceptance" / "stable-v2.0" / "v1.3-regression" / "eval_report.json",
            "stable-v2.0",
            "regression",
        ),
        (
            project_root / "acceptance" / "stable-v2.0.1" / "eval_report.json",
            "stable-v2.0.1",
            "formal_acceptance",
        ),
    ]
    for report_path, milestone, stage in report_rules:
        imported = _index_eval_report(catalog, report_path, milestone, stage)
        counts[milestone] = counts.get(milestone, 0) + imported

    checkpoint_report = project_root / "acceptance" / "stable-v2.0" / "checkpoint_report.json"
    formal_checkpoint_id = ""
    if checkpoint_report.exists():
        report = _read_json(checkpoint_report)
        real_model = report.get("real_model_resume") if isinstance(report, dict) else None
        if isinstance(real_model, dict):
            formal_checkpoint_id = str(real_model.get("run_id") or "")
            if _index_checkpoint_run(
                catalog,
                runs_root,
                formal_checkpoint_id,
                stage="checkpoint_resume",
                result=str(real_model.get("result") or "PASS"),
                report_path=checkpoint_report,
            ):
                counts["stable-v2.0"] = counts.get("stable-v2.0", 0) + 1

    # The controlled real-model rehearsal immediately before the formal V2.0
    # run was intentionally not copied into acceptance. Include it without
    # moving or rewriting the original evidence.
    if formal_checkpoint_id:
        for run_dir in runs_root.iterdir() if runs_root.exists() else ():
            if not run_dir.is_dir() or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", run_dir.name):
                continue
            if run_dir.name >= formal_checkpoint_id:
                continue
            state = _read_json(run_dir / "state.json")
            metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
            if int(metrics.get("checkpoints_created", 0) or 0) <= 0:
                continue
            if _index_checkpoint_run(
                catalog,
                runs_root,
                run_dir.name,
                stage="development_precheck",
                result=_result_from_status(str(state.get("status") or "")),
                report_path=checkpoint_report,
            ):
                counts["stable-v2.0"] = counts.get("stable-v2.0", 0) + 1

    return counts


def _index_eval_report(catalog: RunCatalog, report_path: Path, milestone: str, stage: str) -> int:
    if not report_path.exists():
        return 0
    report = _read_json(report_path)
    configuration = report.get("configuration") if isinstance(report.get("configuration"), dict) else {}
    imported = 0
    for case in report.get("cases", []):
        if not isinstance(case, dict):
            continue
        run_id = str(case.get("run_id") or "")
        run_dir = str(case.get("run_dir") or "")
        if not run_id or not run_dir:
            continue
        metrics = case.get("metrics") if isinstance(case.get("metrics"), dict) else {}
        entry = catalog.upsert(
            milestone,
            {
                "run_id": run_id,
                "run_dir": str(Path(run_dir).resolve()),
                "stage": stage,
                "source": "acceptance",
                "status": str(case.get("run_status") or metrics.get("status") or ""),
                "result": "PASS" if bool(case.get("passed")) else "FAIL",
                "case_name": str(case.get("name") or ""),
                "attempt": int(case.get("attempt", 1) or 1),
                "started_at": metrics.get("started_at") or _timestamp_from_run_id(run_id),
                "completed_at": metrics.get("completed_at"),
                "git_commit": str(configuration.get("git_commit") or ""),
                "report_path": str(report_path.resolve()),
            },
        )
        if entry is not None:
            imported += 1
    return imported


def _index_checkpoint_run(
    catalog: RunCatalog,
    runs_root: Path,
    run_id: str,
    *,
    stage: str,
    result: str,
    report_path: Path,
) -> bool:
    run_dir = runs_root / run_id
    state = _read_json(run_dir / "state.json")
    if not run_id or not run_dir.exists() or not state:
        return False
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    return (
        catalog.upsert(
            "stable-v2.0",
            {
                "run_id": run_id,
                "run_dir": str(run_dir.resolve()),
                "stage": stage,
                "source": "acceptance" if stage == "checkpoint_resume" else "development",
                "status": str(state.get("status") or ""),
                "result": result,
                "case_name": "真实模型Checkpoint恢复",
                "attempt": 1,
                "goal": str(state.get("goal") or ""),
                "started_at": metrics.get("started_at") or _timestamp_from_run_id(run_id),
                "completed_at": metrics.get("completed_at"),
                "report_path": str(report_path.resolve()),
            },
        )
        is not None
    )


def _entry_title(entry: dict[str, Any]) -> str:
    version = _display_milestone(str(entry.get("milestone") or ""))
    stage = str(entry.get("stage_label") or "运行记录")
    case_name = str(entry.get("case_name") or "")
    case = case_name.split("_", 1)[0] if case_name else "RUN"
    attempt = entry.get("attempt")
    result = str(entry.get("result") or entry.get("status") or "UNKNOWN").upper()
    parts = [version, stage, case]
    if attempt not in {None, ""}:
        parts.append(f"第{attempt}轮")
    parts.append(result)
    return "".join(f"[{part}]" for part in parts if part)


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get("started_at") or ""), str(entry.get("run_id") or "")


def _display_milestone(milestone: str) -> str:
    match = re.search(r"v(\d+(?:\.\d+)*)", milestone, flags=re.IGNORECASE)
    return f"V{match.group(1)}" if match else milestone


def _safe_milestone(value: str) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        return ""
    return _safe_component(value)


def _safe_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(".")
    return cleaned[:160] or "未分类"


def _timestamp_from_run_id(run_id: str) -> str:
    match = re.search(r"(\d{8})-(\d{6})", run_id)
    if not match:
        return ""
    try:
        parsed = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return parsed.isoformat()


def _result_from_status(status: str) -> str:
    return {
        "completed": "PASS",
        "failed": "FAIL",
        "waiting_approval": "WAITING",
        "interrupted": "INTERRUPTED",
        "running": "RUNNING",
        "orphaned": "UNKNOWN",
    }.get(status, status.upper() or "UNKNOWN")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schema_version(payload: dict[str, Any], *, default: int) -> int:
    try:
        return int(payload.get("schema_version", default))
    except (TypeError, ValueError):
        return default
