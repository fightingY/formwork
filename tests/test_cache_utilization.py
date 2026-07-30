from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minicc.evals import cache_probe_runner
from minicc.core.context import STABLE_PREFIX
from minicc.evals.assertions import assertion_spec_sha256
from minicc.evals.case import case_authority_bundle_sha256, load_case
from minicc.evals.cache_utilization import (
    LONG_ACTION_SHAPE_SHA256,
    LONG_CASE,
    REQUIRED_CASES,
    SHORT_CASE,
    build_cache_utilization_report,
    failed_criteria,
    write_cache_utilization_report,
)
from minicc.evals.cache_probe_runner import (
    fixed_long_evidence_profile,
    fixed_probe_profile_sha256,
    fixed_probe_request_sha256s,
    fixed_probe_sequence_sha256,
)


def test_long_case_uses_the_action_shape_locked_by_formal_gate() -> None:
    case = load_case(
        Path(__file__).parents[1]
        / "eval_cases"
        / "capability_suite_v1"
        / LONG_CASE
        / "case.yaml"
    )
    shape_assertions = [
        assertion
        for assertion in case.assertions
        if assertion.get("type") == "trace_action_shape"
    ]

    assert len(shape_assertions) == 1
    assert assertion_spec_sha256(shape_assertions[0]) == LONG_ACTION_SHAPE_SHA256


def test_cache_utilization_accepts_two_independent_balanced_rounds(tmp_path: Path) -> None:
    report = build_cache_utilization_report(
        [_round(1, "p1-first"), _round(2, "p2-first")]
    )

    assert report["passed"] is True
    assert report["status"] == "PASS"
    assert report["rounds"][0]["p2"]["fixed"]["weighted_hit_rate"] == 8_800 / 12_000
    assert report["rounds"][0]["p2"]["real"][LONG_CASE][
        "request_detail_complete"
    ] is True
    assert report["rounds"][0]["criteria"]["long_tasks_use_exactly_9_requests"] is True
    assert report["rounds"][0]["criteria"]["long_action_shape_verified"] is True
    assert report["rounds"][0]["criteria"]["long_post_slide_shape_comparable"] is True
    assert report["rounds"][0]["criteria"]["case_authority_profiles_locked"] is True
    assert report["criteria"]["case_authority_profiles_consistent"] is True
    assert report["rounds"][0]["p2"]["fixed"]["cost_estimate"][
        "estimated_amount"
    ] is None

    _materialize_evidence_sources(report, tmp_path / "sources")
    bundle = write_cache_utilization_report(report, tmp_path / "stable-v2.1.2")
    assert bundle.json_path.is_file()
    assert bundle.evidence_path.is_file()
    assert bundle.manifest_path.is_file()
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_commit"] == "abc123"
    assert len(manifest["input_evidence"]) == 8
    assert set(manifest["artifacts"]) == {
        "report_json",
        "report_markdown",
        "evidence_bundle",
    }
    evidence = json.loads(bundle.evidence_path.read_text(encoding="utf-8"))
    assert len(evidence["inputs"]) == 8
    assert {path.name for path in bundle.json_path.parent.iterdir()} == {
        "report.json",
        "report.md",
        "evidence.json",
        "manifest.json",
    }
    markdown = bundle.markdown_path.read_text(encoding="utf-8")
    assert "Per-request evidence" in markdown
    assert "provider price contract" in markdown.lower()
    with pytest.raises(FileExistsError):
        write_cache_utilization_report(report, tmp_path / "stable-v2.1.2")


def test_cache_utilization_verifies_archived_probe_without_live_source(
    monkeypatch,
) -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    monkeypatch.setattr(
        cache_probe_runner,
        "_fixed_long_evidence",
        lambda: (_ for _ in ()).throw(AssertionError("live source read")),
    )

    assert build_cache_utilization_report(rounds)["passed"] is True


def test_cache_utilization_rejects_forged_expected_request_vector() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    configuration = rounds[0][0]["configuration"]
    configuration["expected_request_sha256s"][0] = "f" * 64
    configuration["fixed_probe_profile_sha256"] = fixed_probe_profile_sha256(
        configuration
    )

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert "round-1.fixed_payloads_verified" in failed_criteria(report)


def test_cache_utilization_rejects_cross_round_probe_contract_drift() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    for probe in rounds[1][:2]:
        configuration = probe["configuration"]
        configuration["long_evidence_sha256"] = "f" * 64
        configuration["fixed_probe_profile_sha256"] = fixed_probe_profile_sha256(
            configuration
        )

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert report["criteria"]["locked_configuration_consistent"] is False


def test_cache_utilization_rejects_missed_absolute_target_without_archive(
    tmp_path: Path,
) -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    broken = deepcopy(rounds)
    probe = broken[0][1]
    probe["requests"][1]["cache_hit_tokens"] = 300
    probe["requests"][1]["cache_miss_tokens"] = 700
    metrics = _row_metrics(probe["requests"], steady_offset=2)
    probe["cache"].update(metrics)
    probe["steady_state_cache"].update(
        {
            "prompt_tokens": metrics["steady_state_prompt_tokens"],
            "hit_tokens": metrics["steady_state_hit_tokens"],
            "miss_tokens": (
                metrics["steady_state_prompt_tokens"]
                - metrics["steady_state_hit_tokens"]
            ),
            "weighted_hit_rate": metrics["steady_state_weighted_hit_rate"],
        }
    )
    report = build_cache_utilization_report(broken)

    assert report["passed"] is False
    assert "round-1.p2_fixed_full_chain_at_least_70" in failed_criteria(report)
    with pytest.raises(ValueError, match="failed cache utilization"):
        write_cache_utilization_report(report, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_cache_utilization_rejects_missing_request_level_evidence() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    rounds[1][3]["cases"][0]["request_rows"] = []

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert "round-2.request_detail_complete" in failed_criteria(report)


def test_cache_utilization_rejects_long_task_without_exact_nine_request_shape() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    long_case = next(
        case
        for case in rounds[0][2]["cases"]
        if case["name"] == LONG_CASE
    )
    long_case["metrics"]["cache_metric_requests"] = 8

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert (
        report["rounds"][0]["criteria"]["long_tasks_use_exactly_9_requests"]
        is False
    )
    assert (
        "round-1.long_tasks_use_exactly_9_requests"
        in failed_criteria(report)
    )


def test_cache_utilization_rejects_unverified_long_action_sequence() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    long_case = next(
        case
        for case in rounds[0][2]["cases"]
        if case["name"] == LONG_CASE
    )
    long_case["assertions"][0]["passed"] = False

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert (
        report["rounds"][0]["criteria"]["long_action_shape_verified"]
        is False
    )
    assert (
        "round-1.long_action_shape_verified"
        in failed_criteria(report)
    )


def test_cache_utilization_rejects_wrong_long_action_shape_spec() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    long_case = next(
        case
        for case in rounds[0][2]["cases"]
        if case["name"] == LONG_CASE
    )
    long_case["assertions"][0]["spec_sha256"] = "0" * 64

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert (
        report["rounds"][0]["criteria"]["long_action_shape_verified"]
        is False
    )


def test_cache_utilization_rejects_mismatched_post_slide_shape() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    long_case = next(
        case
        for case in rounds[1][3]["cases"]
        if case["name"] == LONG_CASE
    )
    long_case["request_rows"][-1]["request_index"] = 7

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert (
        report["rounds"][1]["criteria"]["long_post_slide_shape_comparable"]
        is False
    )
    assert (
        "round-2.long_post_slide_shape_comparable"
        in failed_criteria(report)
    )


def test_cache_utilization_rejects_rows_redistributed_between_long_runs() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    long_cases = [
        case
        for case in rounds[0][2]["cases"]
        if case["name"] == LONG_CASE
    ]
    moved = long_cases[0]["request_rows"].pop()
    long_cases[1]["request_rows"].append(moved)

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert (
        report["rounds"][0]["criteria"]["long_tasks_use_exactly_9_requests"]
        is False
    )


def test_cache_utilization_rejects_top_level_failed_or_extra_suite() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    rounds[0][2]["passed"] = False
    rounds[0][2]["result"] = "FAIL"
    rounds[1][3]["cases"].append(
        {
            "name": "unexpected_case",
            "run_id": "unexpected",
            "passed": False,
            "task_success": False,
        }
    )

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert "round-1.suite_top_level_passed" in failed_criteria(report)
    assert "round-2.no_extra_suite_cases" in failed_criteria(report)


def test_cache_utilization_rejects_cross_round_commit_drift_and_forged_attempts() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    rounds[1][1]["configuration"]["git_commit"] = "different-commit"
    for case in rounds[0][3]["cases"]:
        for row in case["request_rows"]:
            row["attempt_count"] = 2
            row["retry_reasons"] = ["timeout"]
        case["metrics"]["provider_request_attempts"] *= 2
        case["metrics"]["provider_retried_requests"] = len(
            case["request_rows"]
        )

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert report["criteria"]["locked_configuration_consistent"] is False
    assert report["rounds"][0]["criteria"]["provider_retries_within_budget"] is True
    assert report["rounds"][0]["criteria"]["retry_cache_penalty_accounted"] is True
    assert report["rounds"][0]["p2"]["real"][LONG_CASE]["hit_tokens"] == 0


def test_cache_utilization_rejects_case_authority_profile_drift() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    changed_fixture = "f" * 64
    for suite in (rounds[1][2], rounds[1][3]):
        for case in suite["cases"]:
            if case["name"] == LONG_CASE:
                case["fixture_content_sha256"] = changed_fixture
        profiles = deepcopy(suite["configuration"]["case_authority_profiles"])
        profiles[LONG_CASE]["fixture_content_sha256"] = changed_fixture
        suite["configuration"]["case_authority_profiles"] = profiles
        suite["configuration"]["case_authority_bundle_sha256"] = (
            case_authority_bundle_sha256(profiles)
        )

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert report["rounds"][1]["criteria"]["case_authority_profiles_locked"] is True
    assert report["criteria"]["case_authority_profiles_consistent"] is False
    assert report["criteria"]["locked_configuration_consistent"] is False


def test_cache_utilization_rejects_forged_prefix_sequence_and_probe_hash() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    rounds[0][1]["requests"][0]["request_sha256"] = "forged"
    for row in rounds[1][3]["cases"][1]["request_rows"]:
        row["request_index"] = 1
        row["prefix_reset_reason"] = None

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert "round-1.fixed_payloads_verified" in failed_criteria(report)
    assert "round-2.request_detail_complete" in failed_criteria(report)


def test_cache_utilization_rejects_relaxed_context_baseline() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    for round_ in rounds:
        for payload in round_:
            payload["configuration"]["recent_turns"] = 100
            payload["configuration"]["max_prompt_chars"] = 1_000_000
            payload["configuration"]["compaction_strategy"] = "disabled"

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert report["criteria"]["locked_configuration_consistent"] is False
    assert all(
        not round_["criteria"]["fixed_payloads_verified"]
        for round_ in report["rounds"]
    )


def test_cache_utilization_rejects_forged_fixed_warmup_window() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    probe = rounds[0][1]
    last = probe["requests"][-1]
    probe["warmup_requests"] = 11
    probe["steady_state_request_count"] = 1
    probe["steady_state_cache"].update(
        {
            "request_count": 1,
            "metric_requests": 1,
            "prompt_tokens": last["prompt_tokens"],
            "hit_tokens": last["cache_hit_tokens"],
            "miss_tokens": last["cache_miss_tokens"],
            "weighted_hit_rate": (
                last["cache_hit_tokens"] / last["prompt_tokens"]
            ),
        }
    )

    report = build_cache_utilization_report(rounds)

    assert report["passed"] is False
    assert (
        report["rounds"][0]["criteria"]["fixed_payloads_verified"] is False
    )


def test_cache_utilization_conservatively_costs_retried_physical_attempts() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    retried = rounds[0][1]["requests"][2]
    retried["attempt_count"] = 2
    retried["retry_reasons"] = ["timeout"]

    report = build_cache_utilization_report(rounds)
    metrics = report["rounds"][0]["p2"]["fixed"]

    assert metrics["logical_prompt_tokens"] == 12_000
    assert metrics["prompt_tokens"] == 13_000
    assert metrics["raw_hit_tokens"] == 8_800
    assert metrics["hit_tokens"] == 8_000
    assert metrics["miss_tokens"] == 5_000
    assert metrics["retry_extra_prompt_tokens"] == 1_000
    assert metrics["retry_penalized_hit_tokens"] == 800
    assert report["rounds"][0]["criteria"]["provider_retries_within_budget"] is True
    assert report["rounds"][0]["criteria"]["retry_cache_penalty_accounted"] is True


def test_cache_utilization_treats_zero_to_zero_short_miss_as_no_regression() -> None:
    rounds = [_round(1, "p1-first"), _round(2, "p2-first")]
    for round_ in rounds:
        for suite in (round_[2], round_[3]):
            for case in suite["cases"]:
                if case["name"] != SHORT_CASE:
                    continue
                for row in case["request_rows"]:
                    row["cache_hit_tokens"] = row["prompt_tokens"]
                    row["cache_miss_tokens"] = 0
                metrics = _row_metrics(case["request_rows"])
                case["metrics"].update(
                    {
                        "cache_observed_hit_tokens": metrics["hit_tokens"],
                        "cache_steady_state_hit_tokens": metrics[
                            "steady_state_hit_tokens"
                        ],
                        "cache_capture_observed_hit_tokens": metrics[
                            "capture_hit_tokens"
                        ],
                    }
                )

    report = build_cache_utilization_report(rounds)

    assert all(
        round_["criteria"]["short_miss_not_regressed"]
        for round_ in report["rounds"]
    )


def _round(index: int, order: str):
    sequence = f"round-{index}"
    return (
        _probe(index, "p1", "append", sequence, order),
        _probe(index, "p2", "epoch", sequence, order),
        _suite(index, "p1", "append", sequence, order),
        _suite(index, "p2", "epoch", sequence, order),
    )


def _configuration(variant: str, layout: str, sequence: str, order: str) -> dict:
    long_profile = fixed_long_evidence_profile()
    return {
        "base_url": "https://provider.test/v1",
        "model": "cache-model",
        "temperature": 0.0,
        "stream": False,
        "include_usage": True,
        "json_mode": True,
        "max_completion_tokens": 256,
        "provider_max_retries": 2,
        "provider_timeout_sec": 300,
        "cache_scope_sha256": "scope",
        "git_commit": "abc123",
        "docker_image": "python@sha256:" + ("a" * 64),
        "milestone": "v2.1.2-development",
        "cache_sequence_id": sequence,
        "execution_order": order,
        "cache_variant": variant,
        "prompt_layout": layout,
        "compaction_strategy": "deterministic",
        "recent_turns": 6,
        "max_prompt_chars": 120_000,
        "release_gate": True,
        "worktree_dirty": False,
        "git_preflight_verified": True,
        "git_postflight_verified": True,
        "system_prefix_sha256": hashlib.sha256(STABLE_PREFIX.encode("utf-8")).hexdigest(),
        "feedback_memory_mode": "disabled",
        "long_evidence_source": long_profile["source"],
        "long_evidence_chars": long_profile["chars"],
        "long_evidence_sha256": long_profile["sha256"],
    }


def _probe(index: int, variant: str, layout: str, sequence: str, order: str) -> dict:
    exact, resets, noncold_hit = (6, 5, 400) if variant == "p1" else (11, 0, 800)
    request_hashes = fixed_probe_request_sha256s(variant, 12, sequence)
    requests = _request_rows(
        12,
        exact_until=exact,
        noncold_hit=noncold_hit,
        request_hashes=request_hashes,
    )
    metrics = _row_metrics(requests, steady_offset=2)
    created_at, completed_at = _interval(index, order, variant, "probe")
    configuration = _configuration(variant, layout, sequence, order)
    configuration["fixed_probe_contract_version"] = 1
    configuration["fixed_probe_repeat"] = 12
    configuration["fixed_probe_warmup_requests"] = 2
    configuration["expected_request_sha256s"] = request_hashes
    configuration["dynamic_sequence_sha256"] = fixed_probe_sequence_sha256(12, sequence)
    configuration["fixed_probe_profile_sha256"] = fixed_probe_profile_sha256(
        configuration
    )
    return {
        "schema_version": 2,
        "probe_id": f"probe-{variant}-{index}",
        "stage": "formal_acceptance",
        "milestone": "v2.1.2-development",
        "created_at": created_at,
        "completed_at": completed_at,
        "configuration": configuration,
        "request_count": 12,
        "warmup_requests": 2,
        "cache": {
            "request_count": 12,
            **metrics,
            "task_success_rate": 1.0,
            "request_success_rate": 1.0,
            "metric_requests": 12,
            "unreported_requests": 0,
            "exact_append_requests": exact,
            "prefix_reset_requests": resets,
            "local_cold_start_requests": 1,
            "latency_ms_total": 12_000,
        },
        "steady_state_request_count": metrics["steady_state_request_count"],
        "steady_state_basis": "configured_warmup_requests",
        "steady_state_cache": {
            "request_count": metrics["steady_state_request_count"],
            "metric_requests": metrics["steady_state_request_count"],
            "unreported_requests": 0,
            "prompt_tokens": metrics["steady_state_prompt_tokens"],
            "hit_tokens": metrics["steady_state_hit_tokens"],
            "miss_tokens": (
                metrics["steady_state_prompt_tokens"]
                - metrics["steady_state_hit_tokens"]
            ),
            "weighted_hit_rate": metrics["steady_state_weighted_hit_rate"],
        },
        "stable_prefix": {"consistent": True, "sha256": "stable-prefix"},
        "passed": True,
        "requests": requests,
        "_evidence_integrity_verified": True,
        "_evidence_source_path": f"C:/evidence/probe-{variant}-{index}/report.json",
        "_evidence_report_sha256": "b" * 64,
        "_evidence_manifest_sha256": "c" * 64,
    }


def _suite(index: int, variant: str, layout: str, sequence: str, order: str) -> dict:
    cases = []
    for attempt in range(1, 4):
        cases.append(_case(index, variant, SHORT_CASE, attempt, requests=5))
        cases.append(_case(index, variant, LONG_CASE, attempt, requests=9))
    created_at, completed_at = _interval(index, order, variant, "suite")
    configuration = _configuration(variant, layout, sequence, order)
    profiles = _authority_profiles()
    configuration["case_authority_profiles"] = profiles
    configuration["case_authority_bundle_sha256"] = (
        case_authority_bundle_sha256(profiles)
    )
    return {
        "schema_version": 2,
        "suite_id": f"suite-{variant}-{index}",
        "stage": "formal_acceptance",
        "milestone": "v2.1.2-development",
        "created_at": created_at,
        "completed_at": completed_at,
        "configuration": configuration,
        "passed": True,
        "result": "PASS",
        "cases": cases,
        "_evidence_integrity_verified": True,
        "_evidence_source_path": f"C:/evidence/suite-{variant}-{index}/report.json",
        "_evidence_report_sha256": "d" * 64,
        "_evidence_manifest_sha256": "e" * 64,
    }


def _case(index: int, variant: str, name: str, attempt: int, *, requests: int) -> dict:
    if name == SHORT_CASE:
        noncold_hits = [200] * (requests - 1) if variant == "p1" else [300] * (requests - 1)
        exact_until = requests - 1
    elif variant == "p1":
        noncold_hits = [400] * 6 + [100] * (requests - 7)
        exact_until = 6
    else:
        noncold_hits = [800] * (requests - 1)
        exact_until = requests - 1
    rows = _request_rows(
        requests,
        exact_until=exact_until,
        noncold_hit=noncold_hits,
    )
    row_metrics = _row_metrics(rows)
    case = {
        "name": name,
        "case_source_path": (
            f"eval_cases/capability_suite_v1/{name}/case.yaml"
        ),
        "fixture_source_path": (
            f"eval_cases/capability_suite_v1/{name}/fixture"
        ),
        "case_definition_sha256": hashlib.sha256(
            f"{name}:case".encode("utf-8")
        ).hexdigest(),
        "fixture_content_sha256": hashlib.sha256(
            f"{name}:fixture".encode("utf-8")
        ).hexdigest(),
        "run_id": f"run-{variant}-{index}-{name}-{attempt}",
        "attempt": attempt,
        "passed": True,
        "task_success": True,
        "formal_metric_eligible": True,
        "run_status": "completed",
        "metrics": {
            "cache_observed_prompt_tokens": row_metrics["prompt_tokens"],
            "cache_observed_hit_tokens": row_metrics["hit_tokens"],
            "cache_steady_state_prompt_tokens": row_metrics["steady_state_prompt_tokens"],
            "cache_steady_state_hit_tokens": row_metrics["steady_state_hit_tokens"],
            "cache_steady_state_request_count": row_metrics[
                "steady_state_request_count"
            ],
            "cache_theoretical_input_tokens": row_metrics["theoretical_input_tokens"],
            "cache_capture_observed_hit_tokens": row_metrics["capture_hit_tokens"],
            "cache_metric_requests": requests,
            "cache_unreported_requests": 0,
            "cache_prefix_cold_start_requests": 1,
            "cache_prefix_exact_append_requests": exact_until,
            "cache_prefix_reset_requests": requests - exact_until - 1,
            "provider_retried_requests": 0,
            "provider_request_attempts": requests,
            "bash_actions": requests - 1,
            "turns": requests,
            "completion_tokens": requests * 20,
            "latency_ms": requests * 1_000,
            "total_duration_ms": requests * 1_100,
            "context_compactions": 0,
            "context_retention_rate": None,
            "provider_response_models": ["cache-model"],
        },
        "request_rows": rows,
    }
    if name == LONG_CASE:
        case["assertions"] = [
            {
                "type": "trace_action_shape",
                "passed": True,
                "message": "locked long action shape observed",
                "spec_sha256": LONG_ACTION_SHAPE_SHA256,
            },
        ]
    return case


def _authority_profiles() -> dict[str, dict[str, str]]:
    return {
        name: {
            "source_path": f"eval_cases/capability_suite_v1/{name}/case.yaml",
            "fixture_source_path": (
                f"eval_cases/capability_suite_v1/{name}/fixture"
            ),
            "case_definition_sha256": hashlib.sha256(
                f"{name}:case".encode("utf-8")
            ).hexdigest(),
            "fixture_content_sha256": hashlib.sha256(
                f"{name}:fixture".encode("utf-8")
            ).hexdigest(),
        }
        for name in REQUIRED_CASES
    }


def _request_rows(
    count: int,
    *,
    exact_until: int,
    noncold_hit: int | list[int],
    request_hashes: list[str] | None = None,
) -> list[dict]:
    rows = []
    for request_index in range(1, count + 1):
        cold = request_index == 1
        exact = not cold and request_index <= exact_until + 1
        hit = 0 if cold else (
            noncold_hit
            if isinstance(noncold_hit, int)
            else noncold_hit[request_index - 2]
        )
        rows.append(
            {
                "request_index": request_index,
                "request_sha256": (
                    request_hashes[request_index - 1] if request_hashes else f"request-{request_index}"
                ),
                "prompt_tokens": 1_000,
                "completion_tokens": 20,
                "cache_hit_tokens": hit,
                "cache_miss_tokens": 1_000 - hit,
                "latency_ms": 1_000,
                "attempt_count": 1,
                "retry_reasons": [],
                "prefix_epoch": 1,
                "local_cold_start": cold,
                "previous_request_is_exact_prefix": exact,
                "prefix_reset_reason": (
                    "cold_start" if cold else "exact_append" if exact else "rolling_window"
                ),
                "lcp_estimated_tokens": 0 if cold else 800,
                "theoretical_input_tokens": 0 if cold else 900,
                "theoretical_token_kind": (
                    "unavailable" if cold else "provider_input_boundary"
                ),
                "capture_efficiency_input": None if cold else 0.89,
                "response_model": "cache-model",
            }
        )
    return rows


def _row_metrics(rows: list[dict], *, steady_offset: int = 1) -> dict:
    prompt = sum(row["prompt_tokens"] for row in rows)
    hit = sum(row["cache_hit_tokens"] for row in rows)
    steady = rows[steady_offset:]
    theoretical = sum(row["theoretical_input_tokens"] for row in rows)
    capture_hit = sum(
        min(row["cache_hit_tokens"], row["theoretical_input_tokens"])
        for row in rows
        if row["theoretical_input_tokens"] > 0
    )
    return {
        "prompt_tokens": prompt,
        "hit_tokens": hit,
        "miss_tokens": prompt - hit,
        "weighted_hit_rate": hit / prompt,
        "steady_state_prompt_tokens": sum(row["prompt_tokens"] for row in steady),
        "steady_state_hit_tokens": sum(row["cache_hit_tokens"] for row in steady),
        "steady_state_request_count": len(steady),
        "steady_state_weighted_hit_rate": (
            sum(row["cache_hit_tokens"] for row in steady)
            / sum(row["prompt_tokens"] for row in steady)
        ),
        "theoretical_input_tokens": theoretical,
        "capture_hit_tokens": capture_hit,
        "capture_efficiency_input": capture_hit / theoretical,
    }


def _interval(index: int, order: str, variant: str, kind: str) -> tuple[str, str]:
    base = datetime(2026, 7, index, tzinfo=timezone.utc)
    first = "p1" if order == "p1-first" else "p2"
    variant_offset = 0 if variant == first else 4
    kind_offset = 0 if kind == "probe" else 2
    start = base + timedelta(hours=variant_offset + kind_offset)
    end = start + timedelta(hours=1)
    return start.isoformat(), end.isoformat()


def _materialize_evidence_sources(report: dict, root: Path) -> None:
    for round_ in report["rounds"]:
        for source in round_["sources"].values():
            source_dir = root / source["id"]
            source_dir.mkdir(parents=True)
            report_path = source_dir / "report.json"
            manifest_path = source_dir / "manifest.json"
            report_bytes = (
                json.dumps(
                    {"entity_type": "test_source_report", "id": source["id"]},
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            manifest_bytes = (
                json.dumps(
                    {"entity_type": "test_source_manifest", "id": source["id"]},
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            report_path.write_bytes(report_bytes)
            manifest_path.write_bytes(manifest_bytes)
            source["path"] = str(report_path)
            source["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
            source["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
