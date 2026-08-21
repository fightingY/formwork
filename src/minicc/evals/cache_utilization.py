from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import gcd
from pathlib import Path
from typing import Any
from uuid import uuid4

from minicc.evals.assertions import assertion_spec_sha256
from minicc.evals.cache_probe_runner import fixed_probe_profile_sha256

SHORT_CASE = "C02_fix_failing_test"
LONG_CASE = "C07_large_log_debugging"
REQUIRED_CASES = (SHORT_CASE, LONG_CASE)
EXPECTED_CASE_SOURCE_PATHS = {
    SHORT_CASE: f"eval_cases/capability_suite_v1/{SHORT_CASE}/case.yaml",
    LONG_CASE: f"eval_cases/capability_suite_v1/{LONG_CASE}/case.yaml",
}
EXPECTED_FIXTURE_SOURCE_PATHS = {
    SHORT_CASE: f"eval_cases/capability_suite_v1/{SHORT_CASE}/fixture",
    LONG_CASE: f"eval_cases/capability_suite_v1/{LONG_CASE}/fixture",
}
LONG_ACTION_SHAPE_SPEC = {
    "type": "trace_action_shape",
    "actions": [
        {
            "command": "python -m unittest discover -s tests",
            "expect_exit_code": 1,
            "artifact_ids": ["art_0001"],
        },
        {
            "command": (
                "grep -m 1 -n 'FIRST_CAUSAL_FAILURE' "
                ".minicc_artifacts/stdout_0001.txt"
            ),
            "expect_exit_code": 0,
        },
        {
            "command": "cat VALIDATION_CONTRACT.md",
            "expect_exit_code": 0,
        },
        {
            "command": "cat tests/test_validator.py",
            "expect_exit_code": 0,
        },
        {
            "command": "cat src/validator.py",
            "expect_exit_code": 0,
        },
        {
            "heredoc_write": {
                "path": "src/validator.py",
                "delimiter": "EOF",
            },
            "expect_exit_code": 0,
        },
        {
            "command": "python -m unittest tests.test_validator -v",
            "expect_exit_code": 0,
        },
        {
            "command": "python -m unittest discover -s tests",
            "expect_exit_code": 0,
        },
    ],
}
LONG_ACTION_SHAPE_SHA256 = assertion_spec_sha256(LONG_ACTION_SHAPE_SPEC)


@dataclass(frozen=True)
class CacheUtilizationBundle:
    json_path: Path
    markdown_path: Path
    evidence_path: Path
    manifest_path: Path


CacheUtilizationRound = tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
]


def build_cache_utilization_report(
    rounds: Sequence[CacheUtilizationRound],
    *,
    required_rounds: int = 2,
    minimum_probe_requests: int = 12,
    minimum_case_attempts: int = 3,
    full_chain_target: float = 0.70,
    steady_state_target: float = 0.80,
    capture_efficiency_target: float = 0.85,
    miss_reduction_target: float = 0.40,
    prompt_inflation_limit: float = 0.10,
    short_miss_inflation_limit: float = 0.15,
    saturated_full_chain_target: float = 0.80,
    saturated_steady_state_target: float = 0.90,
) -> dict[str, Any]:
    if len(rounds) != required_rounds:
        raise ValueError(f"cache utilization requires exactly {required_rounds} rounds")
    round_reports = [
        _build_round(
            index,
            p1_probe,
            p2_probe,
            p1_suite,
            p2_suite,
            minimum_probe_requests=minimum_probe_requests,
            minimum_case_attempts=minimum_case_attempts,
            full_chain_target=full_chain_target,
            steady_state_target=steady_state_target,
            capture_efficiency_target=capture_efficiency_target,
            miss_reduction_target=miss_reduction_target,
            prompt_inflation_limit=prompt_inflation_limit,
            saturated_full_chain_target=saturated_full_chain_target,
            saturated_steady_state_target=saturated_steady_state_target,
        )
        for index, (p1_probe, p2_probe, p1_suite, p2_suite) in enumerate(rounds, start=1)
    ]
    evidence_ids = [
        str(value)
        for round_ in round_reports
        for value in (
            round_["p1_probe_id"],
            round_["p2_probe_id"],
            round_["p1_suite_id"],
            round_["p2_suite_id"],
        )
    ]
    run_ids = [
        str(run["run_id"])
        for round_ in round_reports
        for variant in ("p1", "p2")
        for workload in REQUIRED_CASES
        for run in round_[variant]["real"][workload]["runs"]
    ]
    sequence_ids = [str(round_["cache_sequence_id"]) for round_ in round_reports]
    execution_orders = [str(round_["execution_order"]) for round_ in round_reports]
    all_payloads = [
        payload
        for round_payloads in rounds
        for payload in round_payloads
    ]
    suite_payloads = [
        payload
        for _, _, p1_suite, p2_suite in rounds
        for payload in (p1_suite, p2_suite)
    ]
    case_authority_profiles = _case_authority_profiles(suite_payloads)
    p1_short_prompt_tokens = sum(
        round_["p1"]["real"][SHORT_CASE]["prompt_tokens"]
        for round_ in round_reports
    )
    p2_short_prompt_tokens = sum(
        round_["p2"]["real"][SHORT_CASE]["prompt_tokens"]
        for round_ in round_reports
    )
    p1_short_miss_tokens = sum(
        round_["p1"]["real"][SHORT_CASE]["miss_tokens"]
        for round_ in round_reports
    )
    p2_short_miss_tokens = sum(
        round_["p2"]["real"][SHORT_CASE]["miss_tokens"]
        for round_ in round_reports
    )
    sequence_shapes = {
        re.sub(r"\d+", "#", sequence_id)
        for sequence_id in sequence_ids
        if sequence_id
    }
    global_criteria = {
        "exactly_two_rounds": len(round_reports) == required_rounds,
        "independent_evidence_ids": bool(evidence_ids)
        and all(evidence_ids)
        and len(set(evidence_ids)) == len(evidence_ids),
        "independent_run_ids": bool(run_ids)
        and all(run_ids)
        and len(set(run_ids)) == len(run_ids),
        "independent_sequence_ids": bool(sequence_ids)
        and all(sequence_ids)
        and len(set(sequence_ids)) == len(sequence_ids),
        "balanced_execution_order": set(execution_orders) == {"p1-first", "p2-first"},
        "execution_order_verified": all(
            round_["execution_order_verified"] for round_ in round_reports
        ),
        "sequence_shape_consistent": len(sequence_shapes) == 1,
        "locked_configuration_consistent": _locked_configuration_consistent(
            all_payloads,
            suite_payloads,
        ),
        "case_authority_profiles_consistent": bool(case_authority_profiles),
        "runtime_model_identity_verified": all(
            round_["runtime_model_identity_verified"] for round_ in round_reports
        ),
        "short_balanced_prompt_inflation_within_10": _not_regressed(
            p1_short_prompt_tokens,
            p2_short_prompt_tokens,
            prompt_inflation_limit,
        ),
        "short_balanced_miss_inflation_within_15": _not_regressed(
            p1_short_miss_tokens,
            p2_short_miss_tokens,
            short_miss_inflation_limit,
        ),
        "all_rounds_passed": all(round_["passed"] for round_ in round_reports),
    }
    locked_configuration = _archived_configuration(all_payloads[0])
    passed = all(global_criteria.values())
    return {
        "schema_version": 1,
        "entity_type": "prompt_cache_utilization_report",
        "milestone": "v2.1.2",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "required_rounds": required_rounds,
        "completed_rounds": len(round_reports),
        "targets": {
            "full_chain_hit_rate": full_chain_target,
            "steady_state_hit_rate": steady_state_target,
            "capture_efficiency": capture_efficiency_target,
            "miss_reduction": miss_reduction_target,
            "prompt_inflation_limit": prompt_inflation_limit,
            "short_miss_inflation_limit": short_miss_inflation_limit,
            "saturated_full_chain_hit_rate": saturated_full_chain_target,
            "saturated_steady_state_hit_rate": saturated_steady_state_target,
            "retry_accounting": (
                "upper_bound_attempt_count_times_final_prompt_with_zero_hit"
            ),
        },
        "criteria": global_criteria,
        "locked_configuration": locked_configuration,
        "case_authority_profiles": case_authority_profiles,
        "short_balanced_totals": {
            "p1_prompt_tokens": p1_short_prompt_tokens,
            "p2_prompt_tokens": p2_short_prompt_tokens,
            "p1_miss_tokens": p1_short_miss_tokens,
            "p2_miss_tokens": p2_short_miss_tokens,
        },
        "rounds": round_reports,
    }


def write_cache_utilization_report(
    report: Mapping[str, Any],
    output_dir: Path,
) -> CacheUtilizationBundle:
    if not bool(report.get("passed")):
        raise ValueError("failed cache utilization evidence cannot be written as acceptance")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"cache utilization report already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        json_path = temporary / "report.json"
        markdown_path = temporary / "report.md"
        json_path.write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            format_cache_utilization_markdown(report),
            encoding="utf-8",
        )
        sources = [
            source
            for round_ in report.get("rounds", [])
            if isinstance(round_, Mapping)
            for source in (round_.get("sources") or {}).values()
            if isinstance(source, Mapping)
        ]
        evidence_path = temporary / "evidence.json"
        embedded_inputs = [
            _embedded_evidence(source, index=index)
            for index, source in enumerate(sources)
        ]
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "entity_type": "prompt_cache_utilization_source_evidence",
                    "milestone": "stable-v2.1.2",
                    "source_commit": (report.get("locked_configuration") or {}).get(
                        "git_commit"
                    ),
                    "inputs": embedded_inputs,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path = temporary / "manifest.json"
        manifest = {
            "schema_version": 1,
            "entity_type": "prompt_cache_utilization_acceptance",
            "milestone": "stable-v2.1.2",
            "status": report.get("status"),
            "source_commit": (report.get("locked_configuration") or {}).get(
                "git_commit"
            ),
            "input_evidence": [
                {
                    **dict(source),
                    "embedded_path": f"evidence.json#/inputs/{index}",
                }
                for index, source in enumerate(sources)
            ],
            "artifacts": {
                "report_json": _artifact_record(json_path),
                "report_markdown": _artifact_record(markdown_path),
                "evidence_bundle": _artifact_record(evidence_path),
            },
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return CacheUtilizationBundle(
        json_path=output_dir / "report.json",
        markdown_path=output_dir / "report.md",
        evidence_path=output_dir / "evidence.json",
        manifest_path=output_dir / "manifest.json",
    )


def format_cache_utilization_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# miniCC V2.1.2 Prompt Cache Utilization P1/P2 A/B",
        "",
        f"Status: **{report['status']}**",
        "",
        "| Target | Required |",
        "|---|---:|",
        f"| Full-chain weighted hit rate | {_rate(report['targets']['full_chain_hit_rate'])} |",
        f"| Steady-state weighted hit rate | {_rate(report['targets']['steady_state_hit_rate'])} |",
        f"| Cache capture efficiency | {_rate(report['targets']['capture_efficiency'])} |",
        f"| Uncached-token reduction before saturation | {_rate(report['targets']['miss_reduction'])} |",
        f"| Saturation fallback full-chain hit rate | {_rate(report['targets']['saturated_full_chain_hit_rate'])} |",
        f"| Saturation fallback steady-state hit rate | {_rate(report['targets']['saturated_steady_state_hit_rate'])} |",
        f"| Prompt inflation limit | {_rate(report['targets']['prompt_inflation_limit'])} |",
        f"| Balanced short-task miss inflation limit | {_rate(report['targets']['short_miss_inflation_limit'])} |",
        "",
        "## Case authority profiles",
        "",
    ]
    for name, profile in (report.get("case_authority_profiles") or {}).items():
        lines.append(
            f"- `{name}`: case `{profile['source_path']}`, "
            f"fixture `{profile['fixture_source_path']}`"
        )
    lines.append("")
    for round_ in report["rounds"]:
        lines.extend(
            [
                f"## Round {round_['round']}: {'PASS' if round_['passed'] else 'FAIL'}",
                "",
                f"- Namespace/order: `{round_['cache_sequence_id']}` / `{round_['execution_order']}`",
                f"- P1/P2 probe: `{round_['p1_probe_id']}` / `{round_['p2_probe_id']}`",
                f"- P1/P2 suite: `{round_['p1_suite_id']}` / `{round_['p2_suite_id']}`",
                "",
                "| Workload | Variant | Requests | Prompt | Hit | Miss | Full-chain | "
                "Steady | Capture | Task pass | Prefix resets | Provider latency | E2E wall |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                _metric_row("fixed-long", "P1", round_["p1"]["fixed"]),
                _metric_row("fixed-long", "P2", round_["p2"]["fixed"]),
                _metric_row(SHORT_CASE, "P1", round_["p1"]["real"][SHORT_CASE]),
                _metric_row(SHORT_CASE, "P2", round_["p2"]["real"][SHORT_CASE]),
                _metric_row(LONG_CASE, "P1", round_["p1"]["real"][LONG_CASE]),
                _metric_row(LONG_CASE, "P2", round_["p2"]["real"][LONG_CASE]),
                "",
                "### Gate detail",
                "",
            ]
        )
        for name, passed in round_["criteria"].items():
            lines.append(f"- {'PASS' if passed else 'FAIL'} `{name}`")
        lines.append("")
        lines.extend(["### Per-request evidence", ""])
        for workload, variant, metrics in (
            ("fixed-long", "P1", round_["p1"]["fixed"]),
            ("fixed-long", "P2", round_["p2"]["fixed"]),
            (SHORT_CASE, "P1", round_["p1"]["real"][SHORT_CASE]),
            (SHORT_CASE, "P2", round_["p2"]["real"][SHORT_CASE]),
            (LONG_CASE, "P1", round_["p1"]["real"][LONG_CASE]),
            (LONG_CASE, "P2", round_["p2"]["real"][LONG_CASE]),
        ):
            lines.extend(_request_detail(workload, variant, metrics))
    lines.extend(["## Global criteria", ""])
    for name, passed in report["criteria"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} `{name}`")
    lines.append("")
    return "\n".join(lines)


def failed_criteria(report: Mapping[str, Any]) -> list[str]:
    failed = [
        f"global.{name}"
        for name, passed in (report.get("criteria") or {}).items()
        if not passed
    ]
    for round_ in report.get("rounds", []):
        if not isinstance(round_, Mapping):
            continue
        failed.extend(
            f"round-{round_.get('round')}.{name}"
            for name, passed in (round_.get("criteria") or {}).items()
            if not passed
        )
    return failed


def _build_round(
    index: int,
    p1_probe: Mapping[str, Any],
    p2_probe: Mapping[str, Any],
    p1_suite: Mapping[str, Any],
    p2_suite: Mapping[str, Any],
    *,
    minimum_probe_requests: int,
    minimum_case_attempts: int,
    full_chain_target: float,
    steady_state_target: float,
    capture_efficiency_target: float,
    miss_reduction_target: float,
    prompt_inflation_limit: float,
    saturated_full_chain_target: float,
    saturated_steady_state_target: float,
) -> dict[str, Any]:
    _require_variant(p1_probe, "p1", "append", "probe")
    _require_variant(p2_probe, "p2", "epoch", "probe")
    _require_variant(p1_suite, "p1", "append", "suite")
    _require_variant(p2_suite, "p2", "epoch", "suite")
    comparison_errors = _configuration_errors(p1_probe, p2_probe) + _configuration_errors(
        p1_suite,
        p2_suite,
    )
    p1_fixed = _probe_metrics(p1_probe)
    p2_fixed = _probe_metrics(p2_probe)
    p1_real = _suite_metrics(p1_suite)
    p2_real = _suite_metrics(p2_suite)
    p1_long = p1_real[LONG_CASE]
    p2_long = p2_real[LONG_CASE]
    p1_short = p1_real[SHORT_CASE]
    p2_short = p2_real[SHORT_CASE]
    p1_probe_config = _configuration(p1_probe)
    p2_probe_config = _configuration(p2_probe)
    p1_suite_config = _configuration(p1_suite)
    p2_suite_config = _configuration(p2_suite)
    sequence_values = {
        str(config.get("cache_sequence_id") or "")
        for config in (p1_probe_config, p2_probe_config, p1_suite_config, p2_suite_config)
    }
    order_values = {
        str(config.get("execution_order") or "")
        for config in (p1_probe_config, p2_probe_config, p1_suite_config, p2_suite_config)
    }
    sequence_id = next(iter(sequence_values)) if len(sequence_values) == 1 else ""
    execution_order = next(iter(order_values)) if len(order_values) == 1 else ""
    execution_order_verified = _execution_order_verified(
        execution_order,
        p1_probe,
        p2_probe,
        p1_suite,
        p2_suite,
    )
    runtime_model_identity_verified = _runtime_model_identity_verified(
        p1_probe,
        p2_probe,
        p1_suite,
        p2_suite,
    )
    criteria = {
        "comparable_configuration": not comparison_errors,
        "shared_round_namespace": len(sequence_values) == 1 and bool(sequence_id),
        "shared_execution_order": len(order_values) == 1
        and execution_order in {"p1-first", "p2-first"},
        "execution_order_verified": execution_order_verified,
        "formal_clean_evidence": _formal_evidence(
            p1_probe,
            p2_probe,
            p1_suite,
            p2_suite,
        ),
        "fixed_request_count": p1_fixed["request_count"] == minimum_probe_requests
        and p2_fixed["request_count"] == minimum_probe_requests,
        "fixed_payloads_verified": _fixed_probe_evidence_valid(p1_probe, "p1")
        and _fixed_probe_evidence_valid(p2_probe, "p2"),
        "fixed_metrics_complete": p1_fixed["metrics_complete"]
        and p2_fixed["metrics_complete"],
        "fixed_requests_and_tasks_passed": all(
            metrics["probe_passed"]
            and metrics["request_success_rate"] == 1.0
            and metrics["task_success_rate"] == 1.0
            for metrics in (p1_fixed, p2_fixed)
        ),
        "real_case_matrix_complete": all(
            p1_real[name]["run_count"] == minimum_case_attempts
            and p2_real[name]["run_count"] == minimum_case_attempts
            for name in REQUIRED_CASES
        ),
        "suite_top_level_passed": all(
            suite.get("passed") is True and str(suite.get("result") or "") == "PASS"
            for suite in (p1_suite, p2_suite)
        ),
        "no_extra_suite_cases": all(
            _exact_case_matrix(suite, minimum_case_attempts)
            for suite in (p1_suite, p2_suite)
        ),
        "case_authority_profiles_locked": bool(
            _case_authority_profiles((p1_suite, p2_suite))
        ),
        "runtime_model_identity_verified": runtime_model_identity_verified,
        "all_tasks_passed": all(
            metrics["task_success_rate"] == 1.0
            and metrics["verification_pass_rate"] == 1.0
            for metrics in (
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "all_cache_metrics_complete": all(
            metrics["metrics_complete"]
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "provider_retries_within_budget": all(
            metrics["attempts_within_retry_budget"]
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "p2_fixed_full_chain_at_least_70": _at_least(
            p2_fixed["weighted_hit_rate"],
            full_chain_target,
        ),
        "p2_long_full_chain_at_least_70": _at_least(
            p2_long["weighted_hit_rate"],
            full_chain_target,
        ),
        "p2_fixed_steady_at_least_80": _at_least(
            p2_fixed["steady_state_weighted_hit_rate"],
            steady_state_target,
        ),
        "p2_long_steady_at_least_80": _at_least(
            p2_long["steady_state_weighted_hit_rate"],
            steady_state_target,
        ),
        "p2_fixed_capture_at_least_85": _at_least(
            p2_fixed["capture_efficiency_input"],
            capture_efficiency_target,
        ),
        "p2_long_capture_at_least_85": _at_least(
            p2_long["capture_efficiency_input"],
            capture_efficiency_target,
        ),
        "p2_theoretical_full_chain_qualifies": _at_least(
            p2_fixed["theoretical_full_chain_rate"],
            0.80,
        )
        and _at_least(p2_long["theoretical_full_chain_rate"], 0.80),
        "capture_efficiency_bounded": all(
            _bounded_rate(metrics["capture_efficiency_input"])
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "fixed_miss_improvement_or_saturation_target": (
            _reduction_at_least(
                p1_fixed["miss_tokens"],
                p2_fixed["miss_tokens"],
                miss_reduction_target,
            )
            or (
                _at_least(
                    p1_fixed["weighted_hit_rate"],
                    saturated_full_chain_target,
                )
                and _at_least(
                    p2_fixed["weighted_hit_rate"],
                    saturated_full_chain_target,
                )
                and _at_least(
                    p2_fixed["steady_state_weighted_hit_rate"],
                    saturated_steady_state_target,
                )
                and p2_fixed["miss_tokens"] <= p1_fixed["miss_tokens"]
            )
        ),
        "long_post_slide_miss_reduction_at_least_40": _reduction_at_least(
            p1_long["post_slide_miss_tokens"],
            p2_long["post_slide_miss_tokens"],
            miss_reduction_target,
        ),
        "fixed_prompt_inflation_within_10": _inflation_within(
            p1_fixed["prompt_tokens"],
            p2_fixed["prompt_tokens"],
            prompt_inflation_limit,
        ),
        "long_prompt_inflation_within_10": _inflation_within(
            p1_long["prompt_tokens"],
            p2_long["prompt_tokens"],
            prompt_inflation_limit,
        ),
        "prefix_accounting_complete": all(
            metrics["prefix_accounting_complete"]
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "retry_cache_penalty_accounted": all(
            metrics["retry_cache_penalty_accounted"]
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "request_detail_complete": all(
            metrics["request_detail_complete"]
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "request_aggregates_reconcile": all(
            metrics["request_aggregate_consistent"]
            for metrics in (
                p1_fixed,
                p2_fixed,
                p1_short,
                p2_short,
                p1_long,
                p2_long,
            )
        ),
        "long_tasks_use_exactly_9_requests": all(
            run["requests"] == 9
            and run["request_detail_count"] == 9
            and run["turns"] == 9
            and run["request_indices"] == list(range(1, 10))
            for metrics in (p1_long, p2_long)
            for run in metrics["runs"]
        ),
        "long_action_shape_verified": all(
            run["bash_actions"] == 8
            and run["action_shape_verified"]
            for metrics in (p1_long, p2_long)
            for run in metrics["runs"]
        ),
        "long_post_slide_shape_comparable": (
            p1_long["post_slide_request_count"] == 6
            and p2_long["post_slide_request_count"] == 6
            and all(
                run["post_slide_request_count"] == 2
                for metrics in (p1_long, p2_long)
                for run in metrics["runs"]
            )
        ),
        "p2_key_fact_retention_complete": p2_short["key_fact_retention_rate"] == 1.0
        and p2_long["key_fact_retention_rate"] == 1.0,
    }
    return {
        "round": index,
        "p1_probe_id": str(p1_probe.get("probe_id") or ""),
        "p2_probe_id": str(p2_probe.get("probe_id") or ""),
        "p1_suite_id": str(p1_suite.get("suite_id") or ""),
        "p2_suite_id": str(p2_suite.get("suite_id") or ""),
        "cache_sequence_id": sequence_id,
        "execution_order": execution_order,
        "execution_order_verified": execution_order_verified,
        "runtime_model_identity_verified": runtime_model_identity_verified,
        "comparability_errors": comparison_errors,
        "sources": {
            "p1_probe": _evidence_reference(p1_probe),
            "p2_probe": _evidence_reference(p2_probe),
            "p1_suite": _evidence_reference(p1_suite),
            "p2_suite": _evidence_reference(p2_suite),
        },
        "p1": {"fixed": p1_fixed, "real": p1_real},
        "p2": {"fixed": p2_fixed, "real": p2_real},
        "criteria": criteria,
        "passed": all(criteria.values()),
    }


def _probe_metrics(probe: Mapping[str, Any]) -> dict[str, Any]:
    cache = probe.get("cache")
    cache = cache if isinstance(cache, Mapping) else {}
    steady = probe.get("steady_state_cache")
    steady = steady if isinstance(steady, Mapping) else {}
    request_count = _integer(cache.get("request_count"))
    attempts = sum(
        _integer(request.get("attempt_count"))
        for request in probe.get("requests", [])
        if isinstance(request, Mapping)
    )
    retried = sum(
        _integer(request.get("attempt_count")) > 1
        for request in probe.get("requests", [])
        if isinstance(request, Mapping)
    )
    exact = _integer(cache.get("exact_append_requests"))
    resets = _integer(cache.get("prefix_reset_requests"))
    cold = _integer(cache.get("local_cold_start_requests"))
    request_rows = [
        _normalized_request_row(request)
        for request in probe.get("requests", [])
        if isinstance(request, Mapping)
    ]
    effective = _effective_cache_totals(request_rows)
    effective_steady = _effective_cache_totals(request_rows[2:])
    raw_capture_hit = sum(
        min(
            _integer(request.get("hit_tokens")),
            _integer(request.get("theoretical_input_tokens")),
        )
        for request in request_rows
        if _integer(request.get("theoretical_input_tokens")) > 0
    )
    capture_hit = sum(
        min(
            _integer(request.get("effective_hit_tokens")),
            _integer(request.get("theoretical_input_tokens")),
        )
        for request in request_rows
        if _integer(request.get("theoretical_input_tokens")) > 0
    )
    effective_theoretical_input = sum(
        _integer(request.get("effective_theoretical_input_tokens"))
        for request in request_rows
    )
    effective_theoretical_output = sum(
        _integer(request.get("effective_theoretical_output_tokens"))
        for request in request_rows
    )
    effective_positive_hits = [
        _integer(request.get("effective_hit_tokens"))
        for request in request_rows
        if _integer(request.get("effective_hit_tokens")) > 0
    ]
    effective_empirical_block = 0
    for value in effective_positive_hits:
        effective_empirical_block = (
            value
            if effective_empirical_block == 0
            else gcd(effective_empirical_block, value)
        )
    max_retries = _integer(_configuration(probe).get("provider_max_retries"))
    probe_start = _timestamp(probe.get("created_at"))
    probe_end = _timestamp(probe.get("completed_at"))
    wall_time_ms = (
        round((probe_end - probe_start).total_seconds() * 1000)
        if probe_start is not None and probe_end is not None and probe_end >= probe_start
        else 0
    )
    return {
        "request_count": request_count,
        "prompt_tokens": effective["prompt_tokens"],
        "logical_prompt_tokens": effective["logical_prompt_tokens"],
        "retry_extra_prompt_tokens": effective["retry_extra_prompt_tokens"],
        "completion_tokens": sum(
            _integer(request.get("completion_tokens")) for request in request_rows
        ),
        "raw_hit_tokens": effective["raw_hit_tokens"],
        "hit_tokens": effective["hit_tokens"],
        "miss_tokens": effective["miss_tokens"],
        "retry_penalized_hit_tokens": effective[
            "retry_penalized_hit_tokens"
        ],
        "weighted_hit_rate": (
            effective["hit_tokens"] / effective["prompt_tokens"]
            if effective["prompt_tokens"]
            else None
        ),
        "steady_state_prompt_tokens": effective_steady["prompt_tokens"],
        "steady_state_hit_tokens": effective_steady["hit_tokens"],
        "steady_state_request_count": _integer(
            probe.get("steady_state_request_count")
        ),
        "steady_state_basis": str(
            probe.get("steady_state_basis") or "configured_warmup_requests"
        ),
        "steady_state_weighted_hit_rate": (
            effective_steady["hit_tokens"] / effective_steady["prompt_tokens"]
            if effective_steady["prompt_tokens"]
            else None
        ),
        "raw_theoretical_input_tokens": _integer(
            cache.get("theoretical_input_tokens")
        ),
        "raw_theoretical_output_tokens": _integer(
            cache.get("theoretical_output_tokens")
        ),
        "theoretical_input_tokens": effective_theoretical_input,
        "theoretical_output_tokens": effective_theoretical_output,
        "theoretical_full_chain_rate": (
            effective_theoretical_input / effective["prompt_tokens"]
            if effective["prompt_tokens"]
            else None
        ),
        "capture_efficiency_input": (
            capture_hit / effective_theoretical_input
            if effective_theoretical_input
            else None
        ),
        "capture_efficiency_output": (
            capture_hit / effective_theoretical_output
            if effective_theoretical_output
            else None
        ),
        "raw_capture_hit_tokens": raw_capture_hit,
        "capture_hit_tokens": capture_hit,
        "raw_empirical_hit_block_tokens": _optional_int(
            cache.get("empirical_hit_block_tokens")
        ),
        "empirical_hit_block_tokens": effective_empirical_block or None,
        "task_success_rate": _optional_float(cache.get("task_success_rate")),
        "request_success_rate": _optional_float(cache.get("request_success_rate")),
        "probe_passed": bool(probe.get("passed")),
        "verification_pass_rate": 1.0 if bool(probe.get("passed")) else 0.0,
        "retried_requests": retried,
        "provider_request_attempts": attempts,
        "latency_ms_total": _integer(cache.get("latency_ms_total")),
        "total_duration_ms": wall_time_ms,
        "prefix_reset_requests": resets,
        "exact_append_requests": exact,
        "local_cold_start_requests": cold,
        "prefix_accounting_complete": request_count > 0
        and exact + resets + cold == request_count,
        "metrics_complete": _integer(cache.get("metric_requests")) == request_count
        and _integer(cache.get("unreported_requests")) == 0,
        "request_detail_complete": len(request_rows) == request_count
        and all(_request_row_complete(request) for request in request_rows),
        "single_attempt_requests": bool(request_rows)
        and all(_integer(request.get("attempt_count")) == 1 for request in request_rows),
        "attempts_within_retry_budget": _attempts_within_budget(
            request_rows,
            max_retries=max_retries,
        ),
        "retry_cache_penalty_accounted": (
            effective["retry_penalized_hit_tokens"]
            == sum(
                _integer(request.get("hit_tokens"))
                for request in request_rows
                if _integer(request.get("attempt_count")) > 1
            )
            and effective["retry_extra_prompt_tokens"]
            == sum(
                _integer(request.get("prompt_tokens"))
                * max(_integer(request.get("attempt_count")) - 1, 0)
                for request in request_rows
            )
        ),
        "request_aggregate_consistent": _request_aggregate_consistent(
            request_rows,
            request_count=request_count,
            prompt_tokens=_integer(cache.get("prompt_tokens")),
            hit_tokens=_integer(cache.get("hit_tokens")),
            miss_tokens=_integer(cache.get("miss_tokens")),
            theoretical_input_tokens=_integer(cache.get("theoretical_input_tokens")),
            capture_hit_tokens=raw_capture_hit,
            latency_ms_total=_integer(cache.get("latency_ms_total")),
            provider_request_attempts=attempts,
            retried_requests=retried,
            cold_starts=cold,
            exact_appends=exact,
            prefix_resets=resets,
        ),
        "key_fact_retention_rate": 1.0,
        "cost_estimate": _cost_estimate(
            prompt_tokens=effective["prompt_tokens"],
            hit_tokens=effective["hit_tokens"],
            completion_tokens=sum(
                _integer(request.get("completion_tokens")) for request in request_rows
            ),
        ),
        "requests": request_rows,
        "runs": [],
    }


def _suite_metrics(suite: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    cases = [case for case in suite.get("cases", []) if isinstance(case, Mapping)]
    max_retries = _integer(
        _configuration(suite).get("provider_max_retries")
    )
    return {
        name: _aggregate_cases(
            [
                case
                for case in cases
                if str(case.get("name") or "") == name
            ],
            max_retries=max_retries,
        )
        for name in REQUIRED_CASES
    }


def _aggregate_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    max_retries: int,
) -> dict[str, Any]:
    metrics_list: list[Mapping[str, Any]] = []
    for case in cases:
        metrics = case.get("metrics")
        metrics_list.append(metrics if isinstance(metrics, Mapping) else {})
    raw_prompt = sum(
        _integer(metrics.get("cache_observed_prompt_tokens"))
        for metrics in metrics_list
    )
    raw_hit = sum(
        _integer(metrics.get("cache_observed_hit_tokens"))
        for metrics in metrics_list
    )
    raw_steady_prompt = sum(
        _integer(metrics.get("cache_steady_state_prompt_tokens")) for metrics in metrics_list
    )
    raw_steady_hit = sum(
        _integer(metrics.get("cache_steady_state_hit_tokens")) for metrics in metrics_list
    )
    raw_steady_count = sum(
        _integer(metrics.get("cache_steady_state_request_count")) for metrics in metrics_list
    )
    raw_theoretical = sum(
        _integer(metrics.get("cache_theoretical_input_tokens")) for metrics in metrics_list
    )
    raw_theoretical_output = sum(
        _integer(metrics.get("cache_theoretical_output_tokens"))
        for metrics in metrics_list
    )
    raw_capture_hit = sum(
        _integer(metrics.get("cache_capture_observed_hit_tokens")) for metrics in metrics_list
    )
    request_count = sum(
        _integer(metrics.get("cache_metric_requests"))
        + _integer(metrics.get("cache_unreported_requests"))
        for metrics in metrics_list
    )
    cold = sum(
        _integer(metrics.get("cache_prefix_cold_start_requests")) for metrics in metrics_list
    )
    exact = sum(
        _integer(metrics.get("cache_prefix_exact_append_requests")) for metrics in metrics_list
    )
    resets = sum(
        _integer(metrics.get("cache_prefix_reset_requests")) for metrics in metrics_list
    )
    completion = sum(_integer(metrics.get("completion_tokens")) for metrics in metrics_list)
    latency = sum(_integer(metrics.get("latency_ms")) for metrics in metrics_list)
    total_duration = sum(
        _integer(metrics.get("total_duration_ms")) for metrics in metrics_list
    )
    request_rows_by_run = [_case_request_rows(case) for case in cases]
    flat_request_rows = [
        {"run_id": str(case.get("run_id") or ""), **row}
        for case, rows in zip(cases, request_rows_by_run, strict=True)
        for row in rows
    ]
    effective = _effective_cache_totals(flat_request_rows)
    effective_steady_rows = _observed_steady_rows(flat_request_rows)
    effective_steady = _effective_cache_totals(effective_steady_rows)
    theoretical = sum(
        _integer(row.get("effective_theoretical_input_tokens"))
        for row in flat_request_rows
    )
    theoretical_output = sum(
        _integer(row.get("effective_theoretical_output_tokens"))
        for row in flat_request_rows
    )
    capture_hit = sum(
        min(
            _integer(row.get("effective_hit_tokens")),
            _integer(row.get("effective_theoretical_input_tokens")),
        )
        for row in flat_request_rows
        if _integer(row.get("effective_theoretical_input_tokens")) > 0
    )
    retention_rates = [
        _optional_float(metrics.get("context_retention_rate"))
        for metrics in metrics_list
        if _integer(metrics.get("context_compactions")) > 0
    ]
    if not retention_rates:
        key_fact_retention_rate = 1.0
        key_fact_retention_basis = "no_compaction_no_fact_eviction"
    elif any(value is None for value in retention_rates):
        key_fact_retention_rate = None
        key_fact_retention_basis = "compaction_metric_missing"
    else:
        key_fact_retention_rate = min(value for value in retention_rates if value is not None)
        key_fact_retention_basis = "measured_compaction_retention"
    post_slide_rows = [
        row
        for row in flat_request_rows
        if _integer(row.get("request_index")) >= 8
    ]
    post_slide_prompt = sum(
        _integer(row.get("effective_prompt_tokens"))
        for row in post_slide_rows
    )
    post_slide_hit = sum(
        _integer(row.get("effective_hit_tokens"))
        for row in post_slide_rows
    )
    empirical_blocks = [
        _integer(row.get("effective_hit_tokens"))
        for row in flat_request_rows
        if _integer(row.get("effective_hit_tokens")) > 0
    ]
    empirical_block = 0
    for value in empirical_blocks:
        empirical_block = value if empirical_block == 0 else gcd(empirical_block, value)
    return {
        "run_count": len(cases),
        "request_count": request_count,
        "prompt_tokens": effective["prompt_tokens"],
        "logical_prompt_tokens": effective["logical_prompt_tokens"],
        "retry_extra_prompt_tokens": effective["retry_extra_prompt_tokens"],
        "completion_tokens": completion,
        "raw_hit_tokens": effective["raw_hit_tokens"],
        "hit_tokens": effective["hit_tokens"],
        "miss_tokens": effective["miss_tokens"],
        "retry_penalized_hit_tokens": effective[
            "retry_penalized_hit_tokens"
        ],
        "weighted_hit_rate": (
            effective["hit_tokens"] / effective["prompt_tokens"]
            if effective["prompt_tokens"]
            else None
        ),
        "steady_state_prompt_tokens": effective_steady["prompt_tokens"],
        "steady_state_hit_tokens": effective_steady["hit_tokens"],
        "steady_state_request_count": len(effective_steady_rows),
        "steady_state_basis": "first_observed_cache_hit",
        "steady_state_weighted_hit_rate": (
            effective_steady["hit_tokens"]
            / effective_steady["prompt_tokens"]
            if effective_steady["prompt_tokens"]
            else None
        ),
        "raw_theoretical_input_tokens": raw_theoretical,
        "raw_theoretical_output_tokens": raw_theoretical_output,
        "theoretical_input_tokens": theoretical,
        "theoretical_output_tokens": theoretical_output,
        "theoretical_full_chain_rate": (
            theoretical / effective["prompt_tokens"]
            if effective["prompt_tokens"]
            else None
        ),
        "capture_efficiency_input": capture_hit / theoretical if theoretical else None,
        "capture_efficiency_output": (
            capture_hit / theoretical_output if theoretical_output else None
        ),
        "raw_capture_hit_tokens": raw_capture_hit,
        "capture_hit_tokens": capture_hit,
        "empirical_hit_block_tokens": empirical_block or None,
        "task_success_rate": (
            sum(bool(case.get("task_success")) for case in cases) / len(cases)
            if cases
            else 0.0
        ),
        "verification_pass_rate": (
            sum(bool(case.get("passed")) for case in cases) / len(cases)
            if cases
            else 0.0
        ),
        "retried_requests": sum(
            _integer(metrics.get("provider_retried_requests")) for metrics in metrics_list
        ),
        "provider_request_attempts": sum(
            _integer(metrics.get("provider_request_attempts")) for metrics in metrics_list
        ),
        "latency_ms_total": latency,
        "total_duration_ms": total_duration,
        "prefix_reset_requests": resets,
        "exact_append_requests": exact,
        "local_cold_start_requests": cold,
        "prefix_accounting_complete": request_count > 0
        and exact + resets + cold == request_count,
        "request_detail_complete": len(flat_request_rows) == request_count
        and all(_request_row_complete(request) for request in flat_request_rows),
        "single_attempt_requests": bool(flat_request_rows)
        and all(
            _integer(request.get("attempt_count")) == 1
            for request in flat_request_rows
        ),
        "attempts_within_retry_budget": _attempts_within_budget(
            flat_request_rows,
            max_retries=max_retries,
        ),
        "retry_cache_penalty_accounted": (
            effective["retry_penalized_hit_tokens"]
            == sum(
                _integer(row.get("hit_tokens"))
                for row in flat_request_rows
                if _integer(row.get("attempt_count")) > 1
            )
            and effective["retry_extra_prompt_tokens"]
            == sum(
                _integer(row.get("prompt_tokens"))
                * max(_integer(row.get("attempt_count")) - 1, 0)
                for row in flat_request_rows
            )
        ),
        "request_aggregate_consistent": _request_aggregate_consistent(
            flat_request_rows,
            request_count=request_count,
            prompt_tokens=raw_prompt,
            hit_tokens=raw_hit,
            miss_tokens=max(raw_prompt - raw_hit, 0),
            theoretical_input_tokens=raw_theoretical,
            capture_hit_tokens=raw_capture_hit,
            latency_ms_total=latency,
            provider_request_attempts=sum(
                _integer(metrics.get("provider_request_attempts"))
                for metrics in metrics_list
            ),
            retried_requests=sum(
                _integer(metrics.get("provider_retried_requests"))
                for metrics in metrics_list
            ),
            cold_starts=cold,
            exact_appends=exact,
            prefix_resets=resets,
            steady_state_prompt_tokens=raw_steady_prompt,
            steady_state_hit_tokens=raw_steady_hit,
            steady_state_request_count=raw_steady_count,
        ),
        "key_fact_retention_rate": key_fact_retention_rate,
        "key_fact_retention_basis": key_fact_retention_basis,
        "post_slide_request_count": len(post_slide_rows),
        "post_slide_prompt_tokens": post_slide_prompt,
        "post_slide_hit_tokens": post_slide_hit,
        "post_slide_miss_tokens": max(post_slide_prompt - post_slide_hit, 0),
        "post_slide_weighted_hit_rate": (
            post_slide_hit / post_slide_prompt if post_slide_prompt else None
        ),
        "metrics_complete": request_count > 0
        and all(
            _integer(metrics.get("cache_unreported_requests")) == 0
            and _integer(metrics.get("cache_metric_requests")) > 0
            for metrics in metrics_list
        ),
        "cost_estimate": _cost_estimate(
            prompt_tokens=effective["prompt_tokens"],
            hit_tokens=effective["hit_tokens"],
            completion_tokens=completion,
        ),
        "requests": flat_request_rows,
        "runs": [
            {
                "run_id": str(case.get("run_id") or ""),
                "attempt": _integer(case.get("attempt")),
                "passed": bool(case.get("passed")),
                "task_success": bool(case.get("task_success")),
                "requests": (
                    _integer(metrics.get("cache_metric_requests"))
                    + _integer(metrics.get("cache_unreported_requests"))
                ),
                "bash_actions": _integer(metrics.get("bash_actions")),
                "turns": _integer(metrics.get("turns")),
                "action_shape_verified": _case_assertion_spec_passed(
                    case,
                    assertion_type="trace_action_shape",
                    expected_spec_sha256=LONG_ACTION_SHAPE_SHA256,
                ),
                "post_slide_request_count": sum(
                    _integer(row.get("request_index")) >= 8
                    for row in rows
                ),
                "request_indices": [
                    _integer(row.get("request_index"))
                    for row in rows
                ],
                "prompt_tokens": _integer(metrics.get("cache_observed_prompt_tokens")),
                "hit_tokens": _integer(metrics.get("cache_observed_hit_tokens")),
                "steady_state_hit_rate": _optional_float(
                    metrics.get("cache_steady_state_hit_rate")
                ),
                "capture_efficiency_input": _optional_float(
                    metrics.get("cache_capture_efficiency_input")
                ),
                "prefix_resets": _integer(metrics.get("cache_prefix_reset_requests")),
                "latency_ms": _integer(metrics.get("latency_ms")),
                "request_detail_count": len(rows),
            }
            for case, metrics, rows in zip(
                cases,
                metrics_list,
                request_rows_by_run,
                strict=True,
            )
        ],
    }


def _case_assertion_spec_passed(
    case: Mapping[str, Any],
    *,
    assertion_type: str,
    expected_spec_sha256: str,
) -> bool:
    assertions = case.get("assertions")
    if not isinstance(assertions, list):
        return False
    matching = [
        assertion
        for assertion in assertions
        if isinstance(assertion, Mapping)
        and str(assertion.get("type") or "") == assertion_type
    ]
    return (
        len(matching) == 1
        and matching[0].get("passed") is True
        and str(matching[0].get("spec_sha256") or "")
        == expected_spec_sha256
    )


def _case_request_rows(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    supplied = case.get("request_rows")
    if isinstance(supplied, list):
        return [
            _normalized_request_row(row)
            for row in supplied
            if isinstance(row, Mapping)
        ]
    return []


def _normalized_request_row(record: Mapping[str, Any]) -> dict[str, Any]:
    usage = record.get("usage")
    usage = usage if isinstance(usage, Mapping) else record
    cacheability = record.get("cacheability")
    cacheability = cacheability if isinstance(cacheability, Mapping) else record
    prompt = _optional_int(usage.get("prompt_tokens"))
    hit = _optional_int(
        usage.get("cache_hit_tokens", usage.get("prompt_cache_hit_tokens"))
    )
    miss = _optional_int(
        usage.get("cache_miss_tokens", usage.get("prompt_cache_miss_tokens"))
    )
    if miss is None and prompt is not None and hit is not None:
        miss = max(prompt - hit, 0)
    attempt_count = _optional_int(record.get("attempt_count"))
    attempt_multiplier = max(attempt_count or 1, 1)
    theoretical_input = _optional_int(
        cacheability.get("theoretical_input_tokens")
    )
    theoretical_output = _optional_int(
        cacheability.get("theoretical_output_tokens")
    )
    effective_hit = (
        hit
        if hit is not None and attempt_count == 1
        else 0 if hit is not None else None
    )
    effective_prompt = (
        prompt * attempt_multiplier if prompt is not None else None
    )
    effective_miss = (
        max(effective_prompt - effective_hit, 0)
        if effective_prompt is not None and effective_hit is not None
        else None
    )
    retry_reasons_value = record.get("retry_reasons")
    retry_reasons = (
        [str(reason) for reason in retry_reasons_value if str(reason)]
        if isinstance(retry_reasons_value, list)
        else []
    )
    return {
        "request_index": _optional_int(
            record.get("request_index", cacheability.get("request_index"))
        ),
        "prompt_tokens": prompt,
        "completion_tokens": _optional_int(usage.get("completion_tokens")),
        "hit_tokens": hit,
        "miss_tokens": miss,
        "effective_prompt_tokens": effective_prompt,
        "effective_hit_tokens": effective_hit,
        "effective_miss_tokens": effective_miss,
        "weighted_hit_rate": (
            hit / (hit + miss)
            if hit is not None and miss is not None and hit + miss > 0
            else None
        ),
        "latency_ms": _optional_int(record.get("latency_ms")),
        "attempt_count": attempt_count,
        "retry_reasons": retry_reasons,
        "prefix_epoch": _optional_int(cacheability.get("prefix_epoch")),
        "local_cold_start": _optional_bool(cacheability.get("local_cold_start")),
        "previous_request_is_exact_prefix": _optional_bool(
            cacheability.get("previous_request_is_exact_prefix")
        ),
        "prefix_reset_reason": _optional_text(
            cacheability.get("prefix_reset_reason")
        ),
        "lcp_estimated_tokens": _optional_int(
            cacheability.get("lcp_estimated_tokens")
        ),
        "theoretical_input_tokens": theoretical_input,
        "theoretical_output_tokens": theoretical_output,
        "effective_theoretical_input_tokens": (
            theoretical_input * attempt_multiplier
            if theoretical_input is not None
            else None
        ),
        "effective_theoretical_output_tokens": (
            theoretical_output * attempt_multiplier
            if theoretical_output is not None
            else None
        ),
        "effective_capture_efficiency_input": (
            min(
                effective_hit or 0,
                theoretical_input * attempt_multiplier,
            )
            / (theoretical_input * attempt_multiplier)
            if theoretical_input
            else None
        ),
        "theoretical_token_kind": _optional_text(
            cacheability.get("theoretical_token_kind")
        ),
        "capture_efficiency_input": _optional_float(
            cacheability.get("capture_efficiency_input")
        ),
        "steady_state_request": _optional_bool(
            cacheability.get("steady_state_request")
        ),
        "steady_state_start_request_index": _optional_int(
            cacheability.get("steady_state_start_request_index")
        ),
        "steady_state_basis": _optional_text(
            cacheability.get("steady_state_basis")
        ),
        "response_model": _optional_text(
            record.get("response_model", record.get("model"))
        ),
        "system_fingerprint": _optional_text(record.get("system_fingerprint")),
    }


def _request_row_complete(row: Mapping[str, Any]) -> bool:
    prompt_tokens = _optional_int(row.get("prompt_tokens"))
    hit_tokens = _optional_int(row.get("hit_tokens"))
    miss_tokens = _optional_int(row.get("miss_tokens"))
    latency_ms = _optional_int(row.get("latency_ms"))
    attempt_count = _optional_int(row.get("attempt_count"))
    theoretical_input = _optional_int(row.get("theoretical_input_tokens"))
    retry_reasons = row.get("retry_reasons")
    effective_prompt = _optional_int(row.get("effective_prompt_tokens"))
    effective_hit = _optional_int(row.get("effective_hit_tokens"))
    effective_miss = _optional_int(row.get("effective_miss_tokens"))
    return (
        row.get("request_index") is not None
        and prompt_tokens is not None
        and hit_tokens is not None
        and miss_tokens is not None
        and prompt_tokens >= 0
        and hit_tokens >= 0
        and miss_tokens >= 0
        and prompt_tokens == hit_tokens + miss_tokens
        and latency_ms is not None
        and latency_ms >= 0
        and attempt_count is not None
        and attempt_count >= 1
        and isinstance(retry_reasons, list)
        and len(retry_reasons) == attempt_count - 1
        and effective_prompt == prompt_tokens * attempt_count
        and effective_hit == (hit_tokens if attempt_count == 1 else 0)
        and effective_miss == effective_prompt - effective_hit
        and row.get("prefix_epoch") is not None
        and row.get("local_cold_start") is not None
        and row.get("previous_request_is_exact_prefix") is not None
        and row.get("prefix_reset_reason") is not None
        and theoretical_input is not None
        and theoretical_input >= 0
        and row.get("theoretical_token_kind") is not None
    )


def _effective_cache_totals(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    logical_prompt = sum(_integer(row.get("prompt_tokens")) for row in rows)
    prompt = sum(
        _integer(row.get("effective_prompt_tokens")) for row in rows
    )
    raw_hit = sum(_integer(row.get("hit_tokens")) for row in rows)
    effective_hit = sum(
        _integer(row.get("effective_hit_tokens")) for row in rows
    )
    return {
        "prompt_tokens": prompt,
        "logical_prompt_tokens": logical_prompt,
        "retry_extra_prompt_tokens": max(prompt - logical_prompt, 0),
        "raw_hit_tokens": raw_hit,
        "hit_tokens": effective_hit,
        "miss_tokens": max(prompt - effective_hit, 0),
        "retry_penalized_hit_tokens": max(raw_hit - effective_hit, 0),
    }


def _observed_steady_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("run_id") or "fixed-probe"), []).append(
            row
        )
    steady: list[Mapping[str, Any]] = []
    for group in grouped.values():
        first_hit_offset = next(
            (
                index
                for index, row in enumerate(group)
                if _integer(row.get("effective_hit_tokens")) > 0
            ),
            None,
        )
        if first_hit_offset is not None:
            steady.extend(group[first_hit_offset:])
    return steady


def _attempts_within_budget(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_retries: int,
) -> bool:
    maximum_attempts = max(max_retries, 0) + 1
    return bool(rows) and all(
        1 <= _integer(row.get("attempt_count")) <= maximum_attempts
        for row in rows
    )


def _request_aggregate_consistent(
    rows: Sequence[Mapping[str, Any]],
    *,
    request_count: int,
    prompt_tokens: int,
    hit_tokens: int,
    miss_tokens: int,
    theoretical_input_tokens: int,
    capture_hit_tokens: int,
    latency_ms_total: int,
    provider_request_attempts: int,
    retried_requests: int,
    cold_starts: int,
    exact_appends: int,
    prefix_resets: int,
    steady_state_prompt_tokens: int | None = None,
    steady_state_hit_tokens: int | None = None,
    steady_state_request_count: int | None = None,
) -> bool:
    if len(rows) != request_count or request_count <= 0:
        return False
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("run_id") or "fixed-probe"), []).append(row)
    if any(
        [_integer(row.get("request_index")) for row in group]
        != list(range(1, len(group) + 1))
        for group in groups.values()
    ):
        return False
    actual_cold = sum(_optional_bool(row.get("local_cold_start")) is True for row in rows)
    actual_exact = sum(
        _optional_bool(row.get("previous_request_is_exact_prefix")) is True
        for row in rows
    )
    actual_resets = sum(
        _optional_bool(row.get("local_cold_start")) is False
        and _optional_bool(row.get("previous_request_is_exact_prefix")) is not True
        for row in rows
    )
    actual_capture_hit = sum(
        min(
            _integer(row.get("hit_tokens")),
            _integer(row.get("theoretical_input_tokens")),
        )
        for row in rows
        if _integer(row.get("theoretical_input_tokens")) > 0
    )
    actual_steady_rows: list[Mapping[str, Any]] = []
    for group in groups.values():
        first_hit_offset = next(
            (
                index
                for index, row in enumerate(group)
                if _integer(row.get("hit_tokens")) > 0
            ),
            None,
        )
        if first_hit_offset is not None:
            actual_steady_rows.extend(group[first_hit_offset:])
    actual_steady_prompt = sum(
        _integer(row.get("prompt_tokens")) for row in actual_steady_rows
    )
    actual_steady_hit = sum(
        _integer(row.get("hit_tokens")) for row in actual_steady_rows
    )
    actual_retried = sum(
        _integer(row.get("attempt_count")) > 1 for row in rows
    )
    steady_consistent = (
        (
            steady_state_prompt_tokens is None
            or actual_steady_prompt == steady_state_prompt_tokens
        )
        and (
            steady_state_hit_tokens is None
            or actual_steady_hit == steady_state_hit_tokens
        )
        and (
            steady_state_request_count is None
            or len(actual_steady_rows) == steady_state_request_count
        )
    )
    return (
        sum(_integer(row.get("prompt_tokens")) for row in rows) == prompt_tokens
        and sum(_integer(row.get("hit_tokens")) for row in rows) == hit_tokens
        and sum(_integer(row.get("miss_tokens")) for row in rows) == miss_tokens
        and sum(_integer(row.get("theoretical_input_tokens")) for row in rows)
        == theoretical_input_tokens
        and actual_capture_hit == capture_hit_tokens
        and sum(_integer(row.get("latency_ms")) for row in rows) == latency_ms_total
        and sum(_integer(row.get("attempt_count")) for row in rows)
        == provider_request_attempts
        and actual_retried == retried_requests
        and actual_cold == cold_starts
        and actual_exact == exact_appends
        and actual_resets == prefix_resets
        and steady_consistent
        and all(
            _integer(row.get("prefix_epoch")) >= 1
            and bool(_optional_text(row.get("prefix_reset_reason")))
            for row in rows
        )
    )


def _cost_estimate(
    *,
    prompt_tokens: int,
    hit_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    return {
        "pricing_status": "provider_price_contract_not_configured",
        "currency": None,
        "estimated_amount": None,
        "cache_read_input_tokens": hit_tokens,
        "uncached_input_tokens": max(prompt_tokens - hit_tokens, 0),
        "output_tokens": completion_tokens,
    }


def _require_variant(
    payload: Mapping[str, Any],
    variant: str,
    layout: str,
    label: str,
) -> None:
    config = _configuration(payload)
    actual_variant = str(config.get("cache_variant") or "")
    actual_layout = str(config.get("prompt_layout") or "")
    if actual_variant != variant:
        raise ValueError(f"expected {label} cache_variant={variant}, got {actual_variant!r}")
    if actual_layout != layout:
        raise ValueError(f"expected {label} prompt_layout={layout}, got {actual_layout!r}")


def _configuration(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("configuration")
    return value if isinstance(value, Mapping) else {}


def _case_authority_profiles(
    suites: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    observed: dict[str, set[tuple[str, str]]] = {
        name: set() for name in REQUIRED_CASES
    }
    for suite in suites:
        cases = suite.get("cases")
        if not isinstance(cases, list):
            return {}
        suite_profiles: dict[str, dict[str, str]] = {}
        for case in cases:
            if not isinstance(case, Mapping):
                continue
            name = str(case.get("name") or "")
            if name not in observed:
                continue
            source_path = str(case.get("case_source_path") or "")
            fixture_source_path = str(case.get("fixture_source_path") or "")
            if (
                source_path != EXPECTED_CASE_SOURCE_PATHS[name]
                or fixture_source_path
                != EXPECTED_FIXTURE_SOURCE_PATHS[name]
            ):
                return {}
            profile = {
                "source_path": source_path,
                "fixture_source_path": fixture_source_path,
            }
            existing = suite_profiles.setdefault(name, profile)
            if existing != profile:
                return {}
            observed[name].add((source_path, fixture_source_path))
        if set(suite_profiles) != set(REQUIRED_CASES):
            return {}
    if any(len(values) != 1 for values in observed.values()):
        return {}
    profiles: dict[str, dict[str, str]] = {}
    for name, values in observed.items():
        source_path, fixture_source_path = next(iter(values))
        profiles[name] = {
            "source_path": source_path,
            "fixture_source_path": fixture_source_path,
        }
    return profiles


def _configuration_errors(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[str]:
    left = _configuration(baseline)
    right = _configuration(candidate)
    ignored = {
        "cache_variant",
        "prompt_layout",
        "execution_order",
        "created_at",
        "completed_at",
    }
    keys = {
        "base_url",
        "model",
        "temperature",
        "stream",
        "include_usage",
        "json_mode",
        "provider_max_retries",
        "provider_timeout_sec",
        "cache_scope_sha256",
        "git_commit",
        "docker_image",
        "milestone",
        "cache_sequence_id",
        "compaction_strategy",
        "recent_turns",
        "max_prompt_chars",
        "release_gate",
        "sandbox_mode",
        "execute_local",
        "system_prefix_sha256",
        "dynamic_sequence_sha256",
        "feedback_memory_mode",
        "case_contexts",
        "long_evidence_source",
        "long_evidence_chars",
        "long_evidence_sha256",
        "git_preflight_verified",
        "git_postflight_verified",
    }
    return [
        f"configuration mismatch for {key}: {left.get(key)!r} != {right.get(key)!r}"
        for key in sorted(keys - ignored)
        if left.get(key) != right.get(key)
    ]


def _locked_configuration_consistent(
    payloads: Sequence[Mapping[str, Any]],
    suites: Sequence[Mapping[str, Any]],
) -> bool:
    required_common = (
        "base_url",
        "model",
        "temperature",
        "stream",
        "include_usage",
        "json_mode",
        "provider_max_retries",
        "provider_timeout_sec",
        "cache_scope_sha256",
        "git_commit",
        "milestone",
        "compaction_strategy",
        "recent_turns",
        "max_prompt_chars",
        "release_gate",
        "system_prefix_sha256",
        "feedback_memory_mode",
        "git_preflight_verified",
        "git_postflight_verified",
    )
    for key in required_common:
        values = [_configuration(payload).get(key) for payload in payloads]
        if any(value is None or value == "" for value in values):
            return False
        if len({_canonical_value(value) for value in values}) != 1:
            return False
    probes = [
        payload
        for payload in payloads
        if bool(payload.get("probe_id"))
    ]
    if len(probes) != 4:
        return False
    required_probe_common = (
        "fixed_probe_contract_version",
        "fixed_probe_repeat",
        "fixed_probe_warmup_requests",
        "long_evidence_source",
        "long_evidence_chars",
        "long_evidence_sha256",
    )
    for key in required_probe_common:
        values = [_configuration(probe).get(key) for probe in probes]
        if any(value is None or value == "" for value in values):
            return False
        if len({_canonical_value(value) for value in values}) != 1:
            return False
    expected_context_baseline = {
        "recent_turns": 6,
        "max_prompt_chars": 120_000,
        "compaction_strategy": "deterministic",
    }
    if any(
        _configuration(payload).get(key) != expected
        for payload in payloads
        for key, expected in expected_context_baseline.items()
    ):
        return False
    docker_images = [
        str(_configuration(payload).get("docker_image") or "")
        for payload in suites
    ]
    if (
        not docker_images
        or len(set(docker_images)) != 1
        or re.fullmatch(r".+@sha256:[0-9a-f]{64}", docker_images[0]) is None
    ):
        return False
    return all(
        bool(_configuration(payload).get("release_gate"))
        and not bool(_configuration(payload).get("worktree_dirty"))
        for payload in payloads
    )


def _canonical_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _archived_configuration(payload: Mapping[str, Any]) -> dict[str, Any]:
    configuration = _configuration(payload)
    keys = (
        "base_url",
        "model",
        "temperature",
        "stream",
        "include_usage",
        "json_mode",
        "provider_max_retries",
        "provider_timeout_sec",
        "cache_scope_sha256",
        "git_commit",
        "milestone",
        "compaction_strategy",
        "recent_turns",
        "max_prompt_chars",
        "system_prefix_sha256",
        "feedback_memory_mode",
        "git_preflight_verified",
        "git_postflight_verified",
    )
    return {key: configuration.get(key) for key in keys}


def _evidence_reference(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(payload.get("probe_id") or payload.get("suite_id") or ""),
        "path": str(payload.get("_evidence_source_path") or ""),
        "report_sha256": str(payload.get("_evidence_report_sha256") or ""),
        "manifest_sha256": str(payload.get("_evidence_manifest_sha256") or ""),
    }


def _artifact_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _embedded_evidence(
    source: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    report_path = Path(str(source.get("path") or "")).resolve()
    manifest_path = report_path.parent / "manifest.json"
    try:
        report_bytes = report_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
        report_payload = json.loads(report_bytes)
        manifest_payload = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"cannot embed source evidence {source.get('id') or index}: {report_path}"
        ) from exc
    expected_report_hash = str(source.get("report_sha256") or "")
    expected_manifest_hash = str(source.get("manifest_sha256") or "")
    if (
        hashlib.sha256(report_bytes).hexdigest() != expected_report_hash
        or hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_hash
    ):
        raise ValueError(
            f"source evidence changed before archive: {source.get('id') or index}"
        )
    return {
        "index": index,
        "id": str(source.get("id") or ""),
        "origin_path": str(report_path),
        "report_sha256": expected_report_hash,
        "manifest_sha256": expected_manifest_hash,
        "report": report_payload,
        "manifest": manifest_payload,
    }


def _exact_case_matrix(suite: Mapping[str, Any], attempts: int) -> bool:
    cases = [case for case in suite.get("cases", []) if isinstance(case, Mapping)]
    names = [str(case.get("name") or "") for case in cases]
    return (
        len(cases) == attempts * len(REQUIRED_CASES)
        and set(names) == set(REQUIRED_CASES)
        and all(names.count(name) == attempts for name in REQUIRED_CASES)
        and all(
            {
                _integer(case.get("attempt"))
                for case in cases
                if str(case.get("name") or "") == name
            }
            == set(range(1, attempts + 1))
            for name in REQUIRED_CASES
        )
        and all(
            bool(case.get("formal_metric_eligible"))
            and case.get("passed") is True
            and case.get("task_success") is True
            and str(case.get("run_status") or "") == "completed"
            for case in cases
        )
    )


def _execution_order_verified(
    execution_order: str,
    p1_probe: Mapping[str, Any],
    p2_probe: Mapping[str, Any],
    p1_suite: Mapping[str, Any],
    p2_suite: Mapping[str, Any],
) -> bool:
    p1_interval = _combined_interval((p1_probe, p1_suite))
    p2_interval = _combined_interval((p2_probe, p2_suite))
    if p1_interval is None or p2_interval is None:
        return False
    if execution_order == "p1-first":
        return p1_interval[1] <= p2_interval[0]
    if execution_order == "p2-first":
        return p2_interval[1] <= p1_interval[0]
    return False


def _combined_interval(
    payloads: Sequence[Mapping[str, Any]],
) -> tuple[datetime, datetime] | None:
    starts = [_timestamp(payload.get("created_at")) for payload in payloads]
    ends = [_timestamp(payload.get("completed_at")) for payload in payloads]
    if any(value is None for value in (*starts, *ends)):
        return None
    return min(starts), max(ends)  # type: ignore[type-var, return-value]


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _runtime_model_identity_verified(*payloads: Mapping[str, Any]) -> bool:
    for payload in payloads:
        expected = str(_configuration(payload).get("model") or "")
        if not expected:
            return False
        if "probe_id" in payload:
            models = {
                str(request.get("response_model") or "")
                for request in payload.get("requests", [])
                if isinstance(request, Mapping)
            }
            if models != {expected}:
                return False
            continue
        cases = [case for case in payload.get("cases", []) if isinstance(case, Mapping)]
        for case in cases:
            metrics = case.get("metrics")
            metrics = metrics if isinstance(metrics, Mapping) else {}
            models = {
                str(model)
                for model in metrics.get("provider_response_models", [])
                if str(model)
            }
            if models != {expected}:
                return False
    return True


def _fixed_probe_evidence_valid(
    probe: Mapping[str, Any],
    variant: str,
) -> bool:
    configuration = _configuration(probe)
    sequence_id = str(configuration.get("cache_sequence_id") or "")
    repeat = _integer(probe.get("request_count"))
    recent_turns = _integer(configuration.get("recent_turns"))
    max_prompt_chars = _integer(configuration.get("max_prompt_chars"))
    compaction_strategy = str(configuration.get("compaction_strategy") or "")
    if (
        repeat != 12
        or not sequence_id
        or recent_turns != 6
        or max_prompt_chars != 120_000
        or compaction_strategy != "deterministic"
    ):
        return False
    expected_hashes = configuration.get("expected_request_sha256s")
    if (
        not isinstance(expected_hashes, list)
        or len(expected_hashes) != repeat
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
            for value in expected_hashes
        )
    ):
        return False
    requests = [
        request for request in probe.get("requests", []) if isinstance(request, Mapping)
    ]
    actual_hashes = [str(request.get("request_sha256") or "") for request in requests]
    stable = probe.get("stable_prefix")
    stable = stable if isinstance(stable, Mapping) else {}
    steady = probe.get("steady_state_cache")
    steady = steady if isinstance(steady, Mapping) else {}
    expected_steady = requests[2:]
    expected_steady_prompt = sum(
        _integer(request.get("prompt_tokens")) for request in expected_steady
    )
    expected_steady_hit = sum(
        _integer(request.get("cache_hit_tokens")) for request in expected_steady
    )
    expected_steady_miss = sum(
        _integer(request.get("cache_miss_tokens")) for request in expected_steady
    )
    expected_steady_rate = (
        expected_steady_hit / (expected_steady_hit + expected_steady_miss)
        if expected_steady_hit + expected_steady_miss
        else 0.0
    )
    dynamic_sequence_sha256 = str(
        configuration.get("dynamic_sequence_sha256") or ""
    )
    long_evidence_sha256 = str(
        configuration.get("long_evidence_sha256") or ""
    )
    system_prefix_sha256 = str(
        configuration.get("system_prefix_sha256") or ""
    )
    profile_sha256 = str(
        configuration.get("fixed_probe_profile_sha256") or ""
    )
    return (
        len(requests) == repeat
        and actual_hashes == expected_hashes
        and len(set(actual_hashes)) == repeat
        and _integer(configuration.get("fixed_probe_contract_version")) == 1
        and _integer(configuration.get("fixed_probe_repeat")) == repeat
        and _integer(configuration.get("fixed_probe_warmup_requests")) == 2
        and _integer(probe.get("warmup_requests")) == 2
        and _integer(probe.get("steady_state_request_count")) == repeat - 2
        and probe.get("steady_state_basis") == "configured_warmup_requests"
        and _integer(steady.get("request_count")) == repeat - 2
        and _integer(steady.get("metric_requests")) == repeat - 2
        and _integer(steady.get("unreported_requests")) == 0
        and _integer(steady.get("prompt_tokens")) == expected_steady_prompt
        and _integer(steady.get("hit_tokens")) == expected_steady_hit
        and _integer(steady.get("miss_tokens")) == expected_steady_miss
        and _optional_float(steady.get("weighted_hit_rate"))
        == expected_steady_rate
        and re.fullmatch(r"[0-9a-f]{64}", dynamic_sequence_sha256) is not None
        and configuration.get("long_evidence_source")
        == "src/minicc/evals/cache_probe.py"
        and _integer(configuration.get("long_evidence_chars")) == 8_000
        and re.fullmatch(r"[0-9a-f]{64}", long_evidence_sha256) is not None
        and re.fullmatch(r"[0-9a-f]{64}", system_prefix_sha256) is not None
        and re.fullmatch(r"[0-9a-f]{64}", profile_sha256) is not None
        and profile_sha256 == fixed_probe_profile_sha256(configuration)
        and stable.get("consistent") is True
        and bool(stable.get("sha256"))
        and all(
            _integer(request.get("request_index")) == index
            for index, request in enumerate(requests, start=1)
        )
    )


def _formal_evidence(*payloads: Mapping[str, Any]) -> bool:
    return all(
        payload.get("_evidence_integrity_verified") is True
        and bool(payload.get("_evidence_source_path"))
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(payload.get("_evidence_report_sha256") or ""),
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(payload.get("_evidence_manifest_sha256") or ""),
        )
        is not None
        and _integer(payload.get("schema_version")) >= 2
        and str(payload.get("stage") or "") == "formal_acceptance"
        and str(payload.get("milestone") or "") == "v2.1.2-development"
        and bool(_configuration(payload).get("release_gate"))
        and not bool(_configuration(payload).get("worktree_dirty"))
        and bool(_configuration(payload).get("git_commit"))
        and _configuration(payload).get("git_preflight_verified") is True
        and _configuration(payload).get("git_postflight_verified") is True
        for payload in payloads
    )


def _at_least(value: float | None, target: float) -> bool:
    return value is not None and value >= target


def _reduction_at_least(baseline: int, candidate: int, target: float) -> bool:
    return baseline > 0 and candidate <= baseline * (1.0 - target)


def _inflation_within(baseline: int, candidate: int, limit: float) -> bool:
    return baseline > 0 and candidate <= baseline * (1.0 + limit)


def _not_regressed(baseline: int, candidate: int, limit: float) -> bool:
    if baseline == 0:
        return candidate == 0
    return candidate <= baseline * (1.0 + limit)


def _bounded_rate(value: float | None) -> bool:
    return value is not None and 0.0 <= value <= 1.0


def _metric_row(workload: str, variant: str, metrics: Mapping[str, Any]) -> str:
    return (
        f"| {workload} | {variant} | {metrics.get('request_count', 0)} | "
        f"{metrics.get('prompt_tokens', 0)} | {metrics.get('hit_tokens', 0)} | "
        f"{metrics.get('miss_tokens', 0)} | {_rate(metrics.get('weighted_hit_rate'))} | "
        f"{_rate(metrics.get('steady_state_weighted_hit_rate'))} | "
        f"{_rate(metrics.get('capture_efficiency_input'))} | "
        f"{_rate(metrics.get('task_success_rate'))} | "
        f"{metrics.get('prefix_reset_requests', 0)} | "
        f"{metrics.get('latency_ms_total', 0)} ms | "
        f"{metrics.get('total_duration_ms', 0)} ms |"
    )


def _request_detail(
    workload: str,
    variant: str,
    metrics: Mapping[str, Any],
) -> list[str]:
    lines = [
        f"<details><summary>{workload} {variant} ({metrics.get('request_count', 0)} requests)</summary>",
        "",
        "| Run | # | Logical prompt | Raw hit | Raw miss | Gate prompt | Gate hit | "
        "Gate miss | Epoch | Cold | Exact | Reset | Theoretical | Capture | Latency | "
        "Attempts/reasons |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---:|---|",
    ]
    for request in metrics.get("requests", []):
        if not isinstance(request, Mapping):
            continue
        lines.append(
            f"| {request.get('run_id', '-')} | {request.get('request_index', '-')} | "
            f"{request.get('prompt_tokens', '-')} | {request.get('hit_tokens', '-')} | "
            f"{request.get('miss_tokens', '-')} | "
            f"{request.get('effective_prompt_tokens', '-')} | "
            f"{request.get('effective_hit_tokens', '-')} | "
            f"{request.get('effective_miss_tokens', '-')} | "
            f"{request.get('prefix_epoch', '-')} | {request.get('local_cold_start', '-')} | "
            f"{request.get('previous_request_is_exact_prefix', '-')} | "
            f"{request.get('prefix_reset_reason') or '-'} | "
            f"{request.get('theoretical_input_tokens', '-')} | "
            f"{_rate(request.get('effective_capture_efficiency_input'))} | "
            f"{request.get('latency_ms', '-')} | {request.get('attempt_count', '-')} / "
            f"{','.join(request.get('retry_reasons') or []) or '-'} |"
        )
    cost = metrics.get("cost_estimate")
    cost = cost if isinstance(cost, Mapping) else {}
    lines.extend(
        [
            "",
            "Cache model: "
            f"steady basis `{metrics.get('steady_state_basis') or 'n/a'}`; "
            f"theoretical input/output = {metrics.get('theoretical_input_tokens', 0)} / "
            f"{metrics.get('theoretical_output_tokens', 0)} tokens; "
            f"capture input/output = {_rate(metrics.get('capture_efficiency_input'))} / "
            f"{_rate(metrics.get('capture_efficiency_output'))}; "
            f"empirical hit block = {metrics.get('empirical_hit_block_tokens') or 'n/a'} tokens.",
            "",
            "Retry accounting: "
            f"{metrics.get('retried_requests', 0)} logical requests retried; "
            f"{metrics.get('retry_extra_prompt_tokens', 0)} upper-bound physical input tokens "
            "added; "
            f"{metrics.get('retry_penalized_hit_tokens', 0)} raw hit tokens moved to miss. "
            "Each retried request is conservatively costed as attempt_count × final prompt with "
            "zero effective hit; later requests remain measured normally.",
            "",
            "Cost estimate: monetary amount unavailable because no immutable Provider price "
            f"contract was configured; token basis = {cost.get('cache_read_input_tokens', 0)} "
            f"cached input + {cost.get('uncached_input_tokens', 0)} uncached input + "
            f"{cost.get('output_tokens', 0)} output.",
            "",
            "</details>",
            "",
        ]
    )
    return lines


def _rate(value: object) -> str:
    parsed = _optional_float(value)
    return "n/a" if parsed is None else f"{parsed:.2%}"


def _integer(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    parsed = str(value).strip()
    return parsed or None
