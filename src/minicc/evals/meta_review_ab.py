from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from minicc.meta.reviewer import verify_review_source


def build_meta_review_ab_report(
    disabled_suite: Mapping[str, Any],
    enabled_suite: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    *,
    source_commit: str,
    verification_commit: str | None = None,
    verification_changed_paths: Sequence[str] = (),
    allowed_verification_paths: Sequence[str] = (),
    minimum_attempts: int = 3,
) -> dict[str, Any]:
    verification_commit = verification_commit or source_commit
    disabled_cases = _case_rows(disabled_suite)
    enabled_cases = _case_rows(enabled_suite)
    disabled_names = {str(row.get("name") or "") for row in disabled_cases}
    enabled_names = {str(row.get("name") or "") for row in enabled_cases}
    enabled_run_ids = [str(row.get("run_id") or "") for row in enabled_cases]
    review_run_ids = [str(review.get("source", {}).get("run_id") or "") for review in reviews]
    disabled_rate = _pass_rate(disabled_cases)
    enabled_rate = _pass_rate(enabled_cases)
    comparable = _comparable_configuration(disabled_suite, enabled_suite)
    suite_commits = {
        str(suite.get("configuration", {}).get("git_commit") or "")
        for suite in (disabled_suite, enabled_suite)
    }
    criteria = {
        "source_commit_present": bool(source_commit),
        "source_commit_matches_suites": suite_commits == {source_commit},
        "verification_commit_present": bool(verification_commit),
        "verification_delta_allowed": set(verification_changed_paths).issubset(
            set(allowed_verification_paths)
        ),
        "suite_integrity_verified": bool(disabled_suite.get("_evidence_integrity_verified"))
        and bool(enabled_suite.get("_evidence_integrity_verified")),
        "one_identical_real_case": len(disabled_names) == 1
        and disabled_names == enabled_names
        and "" not in disabled_names,
        "minimum_attempts_each": len(disabled_cases) >= minimum_attempts
        and len(enabled_cases) >= minimum_attempts,
        "independent_runs": bool(disabled_cases and enabled_cases)
        and not ({str(row.get("run_id") or "") for row in disabled_cases} & set(enabled_run_ids)),
        "comparable_configuration": comparable,
        "enabled_pass_rate_not_lower": enabled_rate >= disabled_rate,
        "review_for_every_enabled_run": sorted(review_run_ids) == sorted(enabled_run_ids),
        "reviews_use_model": bool(reviews)
        and all(review.get("invocation", {}).get("used_model") is True for review in reviews),
        "review_implementation_commit_consistent": bool(reviews)
        and all(review.get("implementation_commit") == verification_commit for review in reviews),
        "model_invocation_metrics_present": bool(reviews)
        and all(
            int(review.get("invocation", {}).get("attempt_count") or 0) >= 1
            and int(_review_usage(review).get("total_tokens") or 0) > 0
            for review in reviews
        ),
        "review_bundle_integrity_verified": bool(reviews)
        and all(review.get("_evidence_integrity_verified") is True for review in reviews),
        "review_sources_verified": bool(reviews)
        and all(
            review.get("source_verified_after_review") is True and verify_review_source(review)
            for review in reviews
        ),
        "review_ids_unique": len({str(review.get("review_id") or "") for review in reviews})
        == len(reviews)
        and all(str(review.get("review_id") or "") for review in reviews),
    }
    passed = all(criteria.values())
    return {
        "schema_version": 1,
        "entity_type": "meta_review_ab_report",
        "milestone": "v3.1-meta-review-experimental",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "verification_commit": verification_commit,
        "verification_changed_paths": list(verification_changed_paths),
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "claim_scope": (
            "Model-backed offline meta review is invoked for every enabled run, preserves immutable "
            "source evidence, and does not reduce fixed-case pass rate. Review quality uplift is not claimed."
        ),
        "criteria": criteria,
        "disabled": _arm_summary(disabled_suite, disabled_cases, disabled_rate),
        "enabled": _arm_summary(enabled_suite, enabled_cases, enabled_rate),
        "reviews": [
            {
                "review_id": review.get("review_id"),
                "source_run_id": review.get("source", {}).get("run_id"),
                "source_bundle_sha256": review.get("source", {}).get("bundle_sha256"),
                "used_model": review.get("invocation", {}).get("used_model"),
                "model": review.get("invocation", {}).get("model"),
                "implementation_commit": review.get("implementation_commit"),
                "model_call_count": review.get("invocation", {}).get("model_call_count"),
                "schema_retry_count": review.get("invocation", {}).get("schema_retry_count"),
                "attempt_count": review.get("invocation", {}).get("attempt_count"),
                "total_tokens": _review_usage(review).get("total_tokens"),
                "finding_count": len(review.get("findings", [])),
                "report_sha256": review.get("_evidence_report_sha256"),
                "manifest_sha256": review.get("_evidence_manifest_sha256"),
            }
            for review in reviews
        ],
    }


def write_meta_review_ab_report(report: Mapping[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"meta review A/B report already exists: {output_dir}")
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
                    "schema_version": 1,
                    "entity_type": "meta_review_ab_manifest",
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


def _review_usage(review: Mapping[str, Any]) -> Mapping[str, Any]:
    invocation = review.get("invocation")
    if not isinstance(invocation, Mapping):
        return {}
    usage = invocation.get("usage")
    return usage if isinstance(usage, Mapping) else {}


def _pass_rate(cases: Sequence[Mapping[str, Any]]) -> float:
    return sum(row.get("passed") is True for row in cases) / len(cases) if cases else 0.0


def _arm_summary(
    suite: Mapping[str, Any], cases: Sequence[Mapping[str, Any]], pass_rate: float
) -> dict[str, Any]:
    return {
        "suite_id": suite.get("suite_id"),
        "attempts": len(cases),
        "passed_runs": sum(row.get("passed") is True for row in cases),
        "pass_rate": pass_rate,
        "run_ids": [row.get("run_id") for row in cases],
    }


def _comparable_configuration(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    config_a = a.get("configuration") if isinstance(a.get("configuration"), Mapping) else {}
    config_b = b.get("configuration") if isinstance(b.get("configuration"), Mapping) else {}
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
        "case_authority_bundle_sha256",
    )
    return all(config_a.get(key) == config_b.get(key) for key in keys)


def _format_markdown(report: Mapping[str, Any]) -> str:
    disabled = report["disabled"]
    enabled = report["enabled"]
    lines = [
        "# V3.1 Meta Review A/B",
        "",
        f"Overall: **{report['status']}**",
        f"Execution commit: `{report['source_commit']}`",
        f"Review/verification commit: `{report['verification_commit']}`",
        "",
        str(report["claim_scope"]),
        "",
        "## Arms",
        "",
        f"- Disabled: {disabled['passed_runs']}/{disabled['attempts']} ({disabled['pass_rate']:.3f}), suite `{disabled['suite_id']}`",
        f"- Enabled: {enabled['passed_runs']}/{enabled['attempts']} ({enabled['pass_rate']:.3f}), suite `{enabled['suite_id']}`",
        f"- Model-backed reviews: {len(report['reviews'])}",
        "",
        "## Criteria",
        "",
    ]
    lines.extend(
        f"- {'PASS' if value else 'FAIL'} `{name}`" for name, value in report["criteria"].items()
    )
    lines.extend(["", "## Reviews", ""])
    for review in report["reviews"]:
        lines.append(
            f"- `{review['review_id']}` -> `{review['source_run_id']}`; "
            f"model={review['model']}; findings={review['finding_count']}"
        )
    return "\n".join(lines) + "\n"


def _format_csv(report: Mapping[str, Any]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["arm", "suite_id", "attempts", "passed_runs", "pass_rate", "review_count"])
    for arm in ("disabled", "enabled"):
        row = report[arm]
        writer.writerow(
            [arm, row["suite_id"], row["attempts"], row["passed_runs"], row["pass_rate"], len(report["reviews"]) if arm == "enabled" else 0]
        )
    return buffer.getvalue()


def _artifact_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
