from copy import deepcopy

import pytest

from minicc.evals.cache_ab import (
    _cache_improved,
    _prompt_namespace_matches,
    build_cache_ab_report,
    format_cache_ab_markdown,
    write_cache_ab_report,
)
from minicc.evals.cache_probe import build_cache_probe_report
from minicc.evals.cache_probe_runner import (
    fixed_probe_request_sha256s,
    fixed_probe_sequence_sha256,
)


def test_prompt_namespace_is_required_only_for_cache_sequence() -> None:
    assert _prompt_namespace_matches({"cache_sequence_id": None}, {"prompt_namespace": ""})
    assert _prompt_namespace_matches(
        {"cache_sequence_id": "round-1"},
        {"prompt_namespace": "cache-experiment/round-1"},
    )
    assert not _prompt_namespace_matches(
        {"cache_sequence_id": "round-1"}, {"prompt_namespace": ""}
    )


def test_cache_ab_requires_two_passing_independent_rounds() -> None:
    rounds = [
        _round("r1"),
        _round("r2"),
    ]

    report = build_cache_ab_report(rounds)
    markdown = format_cache_ab_markdown(report)

    assert report["status"] == "PASS"
    assert report["passed"] is True
    assert report["independent_evidence"] is True
    assert report["same_direction"] is True
    assert report["rounds"][0]["p0"]["fixed"]["steady_state_cache"]["hit_tokens"] == 0
    assert report["rounds"][0]["p1"]["fixed"]["steady_state_cache"]["hit_tokens"] == 300
    assert report["rounds"][0]["p1"]["real"]["cache"]["hit_tokens"] == 600
    assert len(report["rounds"][0]["p1"]["fixed"]["requests"]) == 5
    assert len(report["rounds"][0]["p1"]["real"]["run_rows"]) == 3
    assert "fixed_rate_delta=+10.00%" in markdown
    assert "### Fixed probe request detail" in markdown
    assert "### Real C02 run detail" in markdown
    assert "| real | P1 | 6 | 6 | 0 | 600 | 5400 | 10.00% | 6000 | 300/50.0 | 100.00%" in markdown


def test_cache_ab_one_good_round_is_inconclusive() -> None:
    report = build_cache_ab_report([_round("r1")])

    assert report["status"] == "INCONCLUSIVE"
    assert report["passed"] is False
    assert report["rounds"][0]["passed"] is True


def test_cache_ab_unsupported_provider_is_inconclusive() -> None:
    p0_probe, p1_probe, p0_suite, p1_suite = _round("r1")
    p0_probe = _probe("p0", "r1", hit=None)
    p1_probe = _probe("p1", "r1", hit=None)

    report = build_cache_ab_report(
        [
            (p0_probe, p1_probe, p0_suite, p1_suite),
            (
                _probe("p0", "r2", hit=None),
                _probe("p1", "r2", hit=None),
                _suite("p0", "r2", hit=0),
                _suite("p1", "r2", hit=100),
            ),
        ]
    )

    assert report["status"] == "INCONCLUSIVE"
    assert report["passed"] is False
    assert report["rounds"][0]["conclusive"] is False
    assert report["rounds"][0]["criteria"]["cache_metrics_complete"] is False


def test_cache_ab_fails_task_regression_even_when_cache_improves() -> None:
    first = _round("r1")
    second = _round("r2")
    regressed = _suite("p1", "r1", hit=100, passed_runs=2)

    report = build_cache_ab_report(
        [
            (first[0], first[1], first[2], regressed),
            second,
        ]
    )

    assert report["status"] == "FAIL"
    assert report["rounds"][0]["criteria"]["p1_task_pass_rate_not_lower"] is False
    assert report["rounds"][0]["structural_passed"] is False


def test_cache_ab_rejects_wrong_variant() -> None:
    p0_probe, _p1_probe, p0_suite, p1_suite = _round("r1")

    with pytest.raises(ValueError, match="expected p1 probe"):
        build_cache_ab_report([(p0_probe, p0_probe, p0_suite, p1_suite)])


def test_cache_ab_rejects_variant_layout_mismatch() -> None:
    p0_probe, p1_probe, p0_suite, p1_suite = deepcopy(_round("r1"))
    p1_probe["configuration"]["prompt_layout"] = "rebuild"

    with pytest.raises(ValueError, match="prompt_layout=append"):
        build_cache_ab_report([(p0_probe, p1_probe, p0_suite, p1_suite)])


def test_cache_ab_rejects_two_equally_failing_real_suites() -> None:
    rounds = []
    for suffix in ("r1", "r2"):
        p0_probe, p1_probe, _p0_suite, _p1_suite = _round(suffix)
        rounds.append(
            (
                p0_probe,
                p1_probe,
                _suite("p0", suffix, hit=0, passed_runs=0),
                _suite("p1", suffix, hit=100, passed_runs=0),
            )
        )

    report = build_cache_ab_report(rounds)

    assert report["status"] == "FAIL"
    assert report["rounds"][0]["criteria"]["p0_real_suite_all_passed"] is False
    assert report["rounds"][0]["criteria"]["p1_real_suite_all_passed"] is False


def test_cache_ab_rejects_reused_round_namespace() -> None:
    first = _round("r1")
    second = list(deepcopy(_round("r2")))
    for payload in second:
        payload["configuration"]["cache_sequence_id"] = "r1"

    report = build_cache_ab_report([first, tuple(second)])

    assert report["status"] == "FAIL"
    assert report["independent_sequence_ids"] is False


def test_cache_improvement_cannot_be_faked_by_prompt_inflation() -> None:
    assert _cache_improved(
        {"weighted_hit_rate": 0.5, "hit_tokens": 50, "miss_tokens": 50},
        {"weighted_hit_rate": 0.3, "hit_tokens": 60, "miss_tokens": 140},
    ) is False


def test_cache_ab_requires_full_probe_cache_coverage_and_no_retries() -> None:
    first = list(deepcopy(_round("r1")))
    first[0]["cache"]["coverage_status"] = "partial"
    first[0]["requests"][0]["attempt_count"] = 2

    report = build_cache_ab_report([tuple(first), _round("r2")])

    criteria = report["rounds"][0]["criteria"]
    assert criteria["cache_metrics_complete"] is False
    assert criteria["no_retried_provider_requests"] is False


def test_cache_ab_requires_exactly_one_attempt_for_every_probe_request() -> None:
    first = list(deepcopy(_round("r1")))
    first[0]["requests"][0]["attempt_count"] = 0

    report = build_cache_ab_report([tuple(first), _round("r2")])

    assert report["rounds"][0]["criteria"]["no_retried_provider_requests"] is False


def test_cache_ab_rejects_missing_real_prompt_metrics_and_local_runtime() -> None:
    first = list(deepcopy(_round("r1")))
    for suite in first[2:]:
        suite["configuration"]["execute_local"] = True
        suite["configuration"]["sandbox_mode"] = "local"
        suite["configuration"]["docker_image"] = "python:latest"
        for case in suite["cases"]:
            case["metrics"].pop("prompt_tokens")

    report = build_cache_ab_report([tuple(first), _round("r2")])

    criteria = report["rounds"][0]["criteria"]
    assert criteria["formal_locked_docker_runtime"] is False
    assert criteria["real_prompt_token_metrics_complete"] is False
    assert report["passed"] is False


def test_cache_ab_recomputes_probe_request_success_instead_of_trusting_top_level() -> None:
    first = list(deepcopy(_round("r1")))
    first[0]["requests"][0]["task_success"] = False
    first[0]["passed"] = True
    first[0]["result"] = "PASS"

    report = build_cache_ab_report([tuple(first), _round("r2")])

    assert report["rounds"][0]["criteria"]["fixed_sequence_requests_succeeded"] is False


def test_cache_ab_locks_fixed_indices_and_real_c02_attempt_matrix() -> None:
    first = list(deepcopy(_round("r1")))
    first[0]["requests"][0]["request_index"] = 2
    first[2]["cases"][0]["name"] = "C01_repo_understanding"

    report = build_cache_ab_report([tuple(first), _round("r2")])

    criteria = report["rounds"][0]["criteria"]
    assert criteria["fixed_sequence_request_indices_locked"] is False
    assert criteria["required_real_case_matrix"] is False
    assert report["passed"] is False


def test_cache_ab_recomputes_fixed_sequence_digest_and_requires_unique_requests() -> None:
    first = list(deepcopy(_round("r1")))
    first[0]["configuration"]["dynamic_sequence_sha256"] = "forged"
    for request in first[1]["requests"]:
        request["request_sha256"] = "repeated"

    report = build_cache_ab_report([tuple(first), _round("r2")])

    criteria = report["rounds"][0]["criteria"]
    assert criteria["fixed_dynamic_sequence_verified"] is False
    assert criteria["fixed_request_payloads_unique"] is False


def test_cache_ab_rejects_evidence_from_a_different_milestone() -> None:
    rounds = [list(deepcopy(_round("r1"))), list(deepcopy(_round("r2")))]
    for round_ in rounds:
        for payload in round_:
            payload["milestone"] = "not-v2.1.1"
            payload["configuration"]["milestone"] = "not-v2.1.1"

    report = build_cache_ab_report([tuple(rounds[0]), tuple(rounds[1])])

    assert report["passed"] is False
    assert report["rounds"][0]["criteria"]["formal_immutable_evidence"] is False


def test_cache_ab_writer_is_atomic_and_immutable(tmp_path) -> None:
    report = build_cache_ab_report([_round("r1"), _round("r2")])

    bundle = write_cache_ab_report(report, tmp_path / "stable-v2.1.1")

    assert bundle.json_path.is_file()
    assert bundle.markdown_path.is_file()
    assert not list(tmp_path.glob(".*.tmp-*"))
    with pytest.raises(FileExistsError, match="already exists"):
        write_cache_ab_report(report, tmp_path / "stable-v2.1.1")


def _round(suffix: str):
    return (
        _probe("p0", suffix, hit=0),
        _probe("p1", suffix, hit=100),
        _suite("p0", suffix, hit=0),
        _suite("p1", suffix, hit=100),
    )


def _execution_order(suffix: str) -> str:
    return "p0-first" if suffix.endswith("1") else "p1-first"


def _timestamp(variant: str, suffix: str, workload: str, *, completed: bool) -> str:
    order = _execution_order(suffix)
    first = (variant == "p0" and order == "p0-first") or (
        variant == "p1" and order == "p1-first"
    )
    minute = (0 if workload == "probe" else 4) + (0 if first else 2)
    if completed:
        minute += 1
    day = 1 if suffix.endswith("1") else 2
    return f"2026-07-{day:02d}T00:{minute:02d}:00+00:00"


def _probe(variant: str, suffix: str, *, hit: int | None) -> dict:
    records = []
    request_hashes = fixed_probe_request_sha256s(variant, 5, suffix)
    for index in range(1, 6):
        usage = {"prompt_tokens": 1000}
        if hit is not None:
            request_hit = hit if index > 2 else 0
            usage.update(
                {
                    "cache_hit_tokens": request_hit,
                    "cache_miss_tokens": 1000 - request_hit,
                }
            )
        records.append(
            {
                "request_index": index,
                "request_success": True,
                "task_success": True,
                "usage": usage,
                "latency_ms": 100,
                "attempt_count": 1,
                "request_sha256": request_hashes[index - 1],
                "stable_prefix_sha256": f"stable-{variant}",
                "stable_prefix_chars": 400 if variant == "p0" else 800,
                "stable_prefix_estimated_tokens": 100 if variant == "p0" else 200,
                "response_model": "fixed",
                "system_fingerprint": "backend-1",
            }
        )
    report = build_cache_probe_report(
        records,
        configuration={
            "prompt_cache_variant": variant,
            "base_url": "https://provider.test/v1",
            "model": "fixed",
            "temperature": 0,
            "json_mode": True,
            "git_commit": "abc123",
            "system_prefix_sha256": "system",
            "dynamic_sequence_sha256": fixed_probe_sequence_sha256(5, suffix),
            "cache_sequence_id": suffix,
            "stream": True,
            "include_usage": True,
            "provider_max_retries": 2,
            "provider_timeout_sec": 300,
            "recent_turns": 6,
            "max_prompt_chars": 120000,
            "cache_scope_sha256": "scope",
            "execution_order": _execution_order(suffix),
            "release_gate": True,
            "worktree_dirty": False,
            "milestone": "v2.1.1-development",
            "compaction_strategy": "deterministic",
            "prompt_layout": "append" if variant == "p1" else "rebuild",
            "feedback_memory_mode": "disabled",
        },
        probe_id=f"cache-probe-{variant}-{suffix}",
        milestone="v2.1.1-development",
        stage="formal_acceptance",
        created_at=_timestamp(variant, suffix, "probe", completed=False),
        completed_at=_timestamp(variant, suffix, "probe", completed=True),
    )
    report["_evidence_integrity_verified"] = True
    return report


def _suite(
    variant: str,
    suffix: str,
    *,
    hit: int,
    passed_runs: int = 3,
) -> dict:
    cases = []
    for attempt in range(1, 4):
        passed = attempt <= passed_runs
        cases.append(
            {
                "name": "C02_fix_failing_test",
                "attempt": attempt,
                "passed": passed,
                "task_success": passed,
                "agent_success": True,
                "infrastructure_success": True,
                "formal_metric_eligible": True,
                "sandbox_mode": "locked",
                "run_id": f"run-{variant}-{suffix}-{attempt}",
                "metrics": {
                    "cache_metrics_available": True,
                    "cache_metric_requests": 2,
                    "cache_unreported_requests": 0,
                    "cache_observed_hit_tokens": hit * 2,
                    "cache_observed_prompt_tokens": 2000,
                    "prompt_tokens": 2000,
                    "latency_ms": 100,
                    "stable_prefix_sha256": f"stable-{variant}",
                    "stable_prefix_chars": 400 if variant == "p0" else 800,
                    "stable_prefix_estimated_tokens": 100 if variant == "p0" else 200,
                    "provider_request_attempts": 2,
                    "provider_retried_requests": 0,
                    "provider_response_models": ["fixed"],
                    "provider_system_fingerprints": ["backend-1"],
                },
            }
        )
    return {
        "schema_version": 2,
        "entity_type": "suite_report",
        "suite_id": f"suite-{variant}-{suffix}",
        "milestone": "v2.1.1-development",
        "stage": "formal_acceptance",
        "configuration": {
            "prompt_cache_variant": variant,
            "base_url": "https://provider.test/v1",
            "model": "fixed",
            "temperature": 0,
            "json_mode": True,
            "git_commit": "abc123",
            "system_prefix_sha256": "system",
            "compaction_strategy": "deterministic",
            "cache_sequence_id": suffix,
            "stream": True,
            "include_usage": True,
            "provider_max_retries": 2,
            "provider_timeout_sec": 300,
            "recent_turns": 6,
            "max_prompt_chars": 120000,
            "cache_scope_sha256": "scope",
            "execution_order": _execution_order(suffix),
            "release_gate": True,
            "worktree_dirty": False,
            "milestone": "v2.1.1-development",
            "prompt_layout": "append" if variant == "p1" else "rebuild",
            "docker_image": "python:test@sha256:fixed",
            "sandbox_mode": "locked",
            "execute_local": False,
            "case_contexts": {
                "C02_fix_failing_test": {},
            },
            "feedback_memory_mode": "disabled",
        },
        "created_at": _timestamp(variant, suffix, "suite", completed=False),
        "completed_at": _timestamp(variant, suffix, "suite", completed=True),
        "result": "PASS" if passed_runs == 3 else "FAIL",
        "passed": passed_runs == 3,
        "_evidence_integrity_verified": True,
        "cases": cases,
    }
