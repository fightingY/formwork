from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

EXPECTED_CASE = "G01_release_manifest_guidance"
EXPECTED_SKILLS = ["release-manifest"]
EXPECTED_RULES = ["release-legacy-id"]


def build_guidance_ab_report(
    disabled_suite: Mapping[str, Any],
    enabled_suite: Mapping[str, Any],
    *,
    source_commit: str,
    verification_commit: str | None = None,
    execution_verification_changed_paths: tuple[str, ...] | list[str] = (),
    allowed_verification_paths: tuple[str, ...] | list[str] = (),
    minimum_attempts: int = 3,
) -> dict[str, Any]:
    verification_commit = verification_commit or source_commit
    disabled_cases = _case_rows(disabled_suite)
    enabled_cases = _case_rows(enabled_suite)
    disabled_config = _configuration(disabled_suite)
    enabled_config = _configuration(enabled_suite)
    disabled_run_ids = {str(row.get("run_id") or "") for row in disabled_cases}
    enabled_run_ids = {str(row.get("run_id") or "") for row in enabled_cases}
    disabled_rate = _pass_rate(disabled_cases)
    enabled_rate = _pass_rate(enabled_cases)
    disabled_selection = [_selection(row) for row in disabled_cases]
    enabled_selection = [_selection(row) for row in enabled_cases]
    criteria = {
        "source_commit_present": bool(source_commit),
        "source_commit_matches_suites": {
            str(disabled_config.get("git_commit") or ""),
            str(enabled_config.get("git_commit") or ""),
        }
        == {source_commit},
        "verification_commit_present": bool(verification_commit),
        "verification_delta_allowed": set(execution_verification_changed_paths).issubset(
            set(allowed_verification_paths)
        ),
        "suite_integrity_verified": bool(disabled_suite.get("_evidence_integrity_verified"))
        and bool(enabled_suite.get("_evidence_integrity_verified")),
        "canonical_identical_case": _case_names(disabled_cases)
        == _case_names(enabled_cases)
        == {EXPECTED_CASE},
        "minimum_attempts_each": len(disabled_cases) >= minimum_attempts
        and len(enabled_cases) >= minimum_attempts,
        "independent_runs": bool(disabled_run_ids and enabled_run_ids)
        and not (disabled_run_ids & enabled_run_ids),
        "comparable_configuration": _comparable_configuration(disabled_config, enabled_config),
        "variant_identity": disabled_config.get("guidance_variant") == "a0"
        and enabled_config.get("guidance_variant") == "a1",
        "shared_sequence_and_order": bool(disabled_config.get("guidance_sequence_id"))
        and disabled_config.get("guidance_sequence_id")
        == enabled_config.get("guidance_sequence_id")
        and disabled_config.get("guidance_execution_order")
        == enabled_config.get("guidance_execution_order"),
        "disabled_selects_nothing": bool(disabled_selection)
        and all(item == {"skills": [], "rules": []} for item in disabled_selection),
        "enabled_selects_exact_relevant_guidance": bool(enabled_selection)
        and all(
            item == {"skills": EXPECTED_SKILLS, "rules": EXPECTED_RULES}
            for item in enabled_selection
        ),
        "enabled_selection_events_once": bool(enabled_cases)
        and all(_integer(_metrics(row).get("guidance_selection_events")) == 1 for row in enabled_cases),
        "enabled_pass_rate_not_lower": enabled_rate >= disabled_rate,
        "enabled_all_pass": bool(enabled_cases) and enabled_rate == 1.0,
        "enabled_uses_fewer_bash_actions": _arm_bash_actions(enabled_cases)
        <= _arm_bash_actions(disabled_cases) - len(enabled_cases),
        "enabled_uses_fewer_prompt_tokens": _arm_prompt_tokens(enabled_cases)
        < _arm_prompt_tokens(disabled_cases),
        "no_provider_or_protocol_failures": all(
            _integer(_metrics(row).get(key)) == 0
            for row in (*disabled_cases, *enabled_cases)
            for key in ("provider_errors", "protocol_errors")
        ),
    }
    passed = all(criteria.values())
    return {
        "schema_version": 2,
        "entity_type": "guidance_ab_report",
        "milestone": "stable-v3.2",
        "created_at": datetime.now(UTC).isoformat(),
        "source_commit": source_commit,
        "verification_commit": verification_commit,
        "execution_verification_changed_paths": list(execution_verification_changed_paths),
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "claim_scope": (
            "Goal-relevant Skill catalog and commit-bound Feedback rules are selected with exact "
            "precision on the canonical case without reducing task pass rate. Automatic feedback "
            "extraction, ambient retrieval, RAG, and cross-task quality uplift are not claimed."
        ),
        "criteria": criteria,
        "disabled": _arm_summary(disabled_suite, disabled_cases),
        "enabled": _arm_summary(enabled_suite, enabled_cases),
    }


def write_guidance_ab_report(report: Mapping[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"guidance A/B report already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir()
    try:
        json_path = temporary / "report.json"
        md_path = temporary / "report.md"
        csv_path = temporary / "report.csv"
        manifest_path = temporary / "manifest.json"
        json_path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(_format_markdown(report), encoding="utf-8")
        csv_path.write_text(_format_csv(report), encoding="utf-8")
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "entity_type": "guidance_ab_manifest",
                    "milestone": report.get("milestone"),
                    "source_commit": report.get("source_commit"),
                    "verification_commit": report.get("verification_commit"),
                    "status": report.get("status"),
                    "artifacts": {
                        "report_json": _artifact_record(json_path),
                        "report_markdown": _artifact_record(md_path),
                        "report_csv": _artifact_record(csv_path),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {name: output_dir / name for name in ("report.json", "report.md", "report.csv", "manifest.json")}


def _case_rows(suite: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [row for row in suite.get("cases", []) if isinstance(row, Mapping)]


def _configuration(suite: Mapping[str, Any]) -> Mapping[str, Any]:
    value = suite.get("configuration")
    return value if isinstance(value, Mapping) else {}


def _metrics(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("metrics")
    return value if isinstance(value, Mapping) else {}


def _selection(row: Mapping[str, Any]) -> dict[str, list[str]]:
    metrics = _metrics(row)
    return {
        "skills": [str(value) for value in metrics.get("guidance_skill_names", [])],
        "rules": [str(value) for value in metrics.get("guidance_feedback_rule_ids", [])],
    }


def _case_names(cases: list[Mapping[str, Any]]) -> set[str]:
    return {str(row.get("name") or "") for row in cases}


def _pass_rate(cases: list[Mapping[str, Any]]) -> float:
    return sum(row.get("passed") is True for row in cases) / len(cases) if cases else 0.0


def _comparable_configuration(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    keys = (
        "model",
        "temperature",
        "stream",
        "sandbox_mode",
        "execute_local",
        "json_mode",
        "provider_max_retries",
        "provider_timeout_sec",
        "docker_image",
        "git_commit",
        "guidance_feedback_path",
    )
    return all(a.get(key) == b.get(key) for key in keys)


def _arm_summary(suite: Mapping[str, Any], cases: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "suite_id": suite.get("suite_id"),
        "attempts": len(cases),
        "passed_runs": sum(row.get("passed") is True for row in cases),
        "pass_rate": _pass_rate(cases),
        "bash_actions": _arm_bash_actions(cases),
        "prompt_tokens": _arm_prompt_tokens(cases),
        "run_ids": [row.get("run_id") for row in cases],
        "selections": [_selection(row) for row in cases],
    }


def _arm_bash_actions(cases: list[Mapping[str, Any]]) -> int:
    return sum(_integer(_metrics(row).get("bash_actions")) for row in cases)


def _arm_prompt_tokens(cases: list[Mapping[str, Any]]) -> int:
    return sum(_integer(_metrics(row).get("prompt_tokens")) for row in cases)


def _format_markdown(report: Mapping[str, Any]) -> str:
    disabled = report["disabled"]
    enabled = report["enabled"]
    lines = [
        "# V3.2 Skill/Feedback Guidance A/B",
        "",
        f"Overall: **{report['status']}**",
        f"Execution commit: `{report['source_commit']}`",
        f"Verification commit: `{report['verification_commit']}`",
        "",
        str(report["claim_scope"]),
        "",
        "## Arms",
        "",
        f"- A0 disabled: {disabled['passed_runs']}/{disabled['attempts']}, suite `{disabled['suite_id']}`",
        f"- A1 enabled: {enabled['passed_runs']}/{enabled['attempts']}, suite `{enabled['suite_id']}`",
        "",
        "## Criteria",
        "",
    ]
    lines.extend(f"- {'PASS' if value else 'FAIL'} `{name}`" for name, value in report["criteria"].items())
    return "\n".join(lines) + "\n"


def _format_csv(report: Mapping[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=("arm", "suite_id", "attempts", "passed_runs", "pass_rate", "bash_actions", "prompt_tokens"),
        lineterminator="\n",
    )
    writer.writeheader()
    for name in ("disabled", "enabled"):
        arm = report[name]
        writer.writerow({"arm": name, **{key: arm[key] for key in writer.fieldnames if key != "arm"}})
    return output.getvalue()


def _artifact_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _integer(value: object) -> int:
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, bytes, bytearray)):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
