from __future__ import annotations

import json

from minicc.evals.meta_review_ab import build_meta_review_ab_report, write_meta_review_ab_report
from minicc.meta.reviewer import MetaReviewer


def _make_run(tmp_path, run_id: str):
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    for name, payload in {
        "state.json": {"status": "completed", "run_id": run_id},
        "metrics.json": {"status": "completed", "turns": 2},
        "run_report.json": {"result": "PASS", "run_id": run_id},
    }.items():
        (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "trace.jsonl").write_text('{"event":"run_completed"}\n', encoding="utf-8")
    (run_dir / "artifacts" / "diff.patch").write_text("+ok\n", encoding="utf-8")
    return run_dir


def _suite(suite_id: str, run_ids: list[str]):
    return {
        "suite_id": suite_id,
        "_evidence_integrity_verified": True,
        "configuration": {
            "model": "m",
            "temperature": 0.0,
            "stream": True,
            "sandbox_mode": "locked",
            "execute_local": False,
            "json_mode": True,
            "provider_max_retries": 2,
            "provider_timeout_sec": 300.0,
            "docker_image": "python@sha256:x",
            "git_commit": "abc",
            "case_authority_bundle_sha256": "authority",
        },
        "cases": [
            {"name": "C02_fix_failing_test", "run_id": run_id, "passed": True}
            for run_id in run_ids
        ],
    }


def test_meta_review_ab_requires_actual_review_for_each_enabled_run(tmp_path) -> None:
    reviews = []
    for run_id in ("e1", "e2", "e3"):
        report = MetaReviewer().review_run(
            _make_run(tmp_path, run_id), output_root=tmp_path / "reviews", offline=True
        ).report
        report["invocation"]["used_model"] = True
        report["invocation"]["mode"] = "model"
        report["invocation"]["model"] = "m"
        report["invocation"]["attempt_count"] = 1
        report["invocation"]["usage"] = {"total_tokens": 10}
        report["implementation_commit"] = "def"
        report["_evidence_integrity_verified"] = True
        reviews.append(report)

    result = build_meta_review_ab_report(
        _suite("disabled", ["d1", "d2", "d3"]),
        _suite("enabled", ["e1", "e2", "e3"]),
        reviews,
        source_commit="abc",
        verification_commit="def",
        verification_changed_paths=["src/minicc/meta/reviewer.py"],
        allowed_verification_paths=["src/minicc/meta/reviewer.py"],
    )

    assert result["passed"] is True
    assert result["disabled"]["pass_rate"] == 1.0
    assert result["enabled"]["pass_rate"] == 1.0
    assert result["criteria"]["review_for_every_enabled_run"] is True
    bundle = write_meta_review_ab_report(result, tmp_path / "acceptance")
    assert set(bundle) == {"report.json", "report.md", "report.csv", "manifest.json"}
    assert {path.name for path in (tmp_path / "acceptance").iterdir()} == set(bundle)


def test_meta_review_ab_fails_when_one_enabled_run_was_not_reviewed(tmp_path) -> None:
    review = MetaReviewer().review_run(
        _make_run(tmp_path, "e1"), output_root=tmp_path / "reviews", offline=True
    ).report
    review["invocation"]["used_model"] = True
    review["invocation"]["attempt_count"] = 1
    review["invocation"]["usage"] = {"total_tokens": 10}
    review["implementation_commit"] = "def"
    review["_evidence_integrity_verified"] = True

    result = build_meta_review_ab_report(
        _suite("disabled", ["d1", "d2", "d3"]),
        _suite("enabled", ["e1", "e2", "e3"]),
        [review],
        source_commit="abc",
        verification_commit="def",
        verification_changed_paths=["src/minicc/meta/reviewer.py"],
        allowed_verification_paths=["src/minicc/meta/reviewer.py"],
    )

    assert result["passed"] is False
    assert result["criteria"]["review_for_every_enabled_run"] is False
