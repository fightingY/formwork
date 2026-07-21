from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CompactionABBundle:
    json_path: Path
    markdown_path: Path


def build_compaction_ab_report(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    repeated_io_tolerance: float = 0.25,
) -> dict[str, Any]:
    if not pairs:
        raise ValueError("at least one A0/A1 suite pair is required")
    rounds = []
    for index, (a0_suite, a1_suite) in enumerate(pairs, start=1):
        _validate_variant(a0_suite, "a0")
        _validate_variant(a1_suite, "a1")
        comparability_errors = _comparability_errors(a0_suite, a1_suite)
        a0 = _variant_metrics(a0_suite, semantic=False)
        a1 = _variant_metrics(a1_suite, semantic=True)
        prompt_reduction = (
            (a0["prompt_chars_mean"] - a1["prompt_chars_mean"]) / a0["prompt_chars_mean"]
            if a0["prompt_chars_mean"] > 0
            else 0.0
        )
        io_limit = max(
            a0["repeated_io_mean"] * (1 + repeated_io_tolerance),
            a0["repeated_io_mean"] + 1.0,
        )
        criteria = {
            "comparable_configuration": not comparability_errors,
            "a0_budget_triggered_in_every_run": a0["all_runs_triggered"],
            "a1_semantic_compaction_triggered_in_every_run": a1["all_runs_compacted"],
            "a1_pass_rate_not_lower": a1["pass_rate"] >= a0["pass_rate"],
            "a1_case_pass_rates_not_lower": all(
                a1["case_pass_rates"].get(name, 0.0) >= rate
                for name, rate in a0["case_pass_rates"].items()
            ),
            "a1_prompt_mean_lower": a1["prompt_chars_mean"] < a0["prompt_chars_mean"],
            "critical_fact_retention_100_percent": (
                a0["retention_eligible"]
                and a1["retention_eligible"]
                and a0["retention_rate"] == 1.0
                and a1["retention_rate"] == 1.0
            ),
            "repeated_io_not_significantly_higher": a1["repeated_io_mean"] <= io_limit,
        }
        rounds.append(
            {
                "round": index,
                "a0_suite_id": a0_suite.get("suite_id"),
                "a1_suite_id": a1_suite.get("suite_id"),
                "comparability_errors": comparability_errors,
                "a0": a0,
                "a1": a1,
                "prompt_reduction_rate": prompt_reduction,
                "repeated_io_limit": io_limit,
                "criteria": criteria,
                "passed": all(criteria.values()),
            }
        )

    required_rounds = 2
    enough_rounds = len(rounds) >= required_rounds
    suite_ids = [suite_id for round_ in rounds for suite_id in (round_["a0_suite_id"], round_["a1_suite_id"])]
    independent_suite_ids = all(suite_ids) and len(set(suite_ids)) == len(suite_ids)
    same_direction = all(round_["criteria"]["a1_prompt_mean_lower"] for round_ in rounds)
    passed = (
        enough_rounds
        and independent_suite_ids
        and same_direction
        and all(round_["passed"] for round_ in rounds)
    )
    status = (
        "PASS"
        if passed
        else "INCONCLUSIVE"
        if not enough_rounds and all(r["passed"] for r in rounds)
        else "FAIL"
    )
    return {
        "schema_version": 1,
        "entity_type": "compaction_ab_report",
        "milestone": "v2.1",
        "status": status,
        "passed": passed,
        "required_rounds": required_rounds,
        "completed_rounds": len(rounds),
        "independent_suite_ids": independent_suite_ids,
        "same_direction": same_direction,
        "repeated_io_tolerance": repeated_io_tolerance,
        "rounds": rounds,
    }


def load_suite_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"not a suite report: {path}")
    return payload


def write_compaction_ab_report(report: dict[str, Any], output_dir: Path) -> CompactionABBundle:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"Compaction A/B report already exists: {output_dir}")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(format_compaction_ab_markdown(report), encoding="utf-8")
    return CompactionABBundle(json_path=json_path, markdown_path=markdown_path)


def format_compaction_ab_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# miniCC V2.1 Context Compaction A/B",
        "",
        f"Status: **{report['status']}**",
        f"Independent rounds: {report['completed_rounds']}/{report['required_rounds']}",
        f"Unique suite evidence: {'yes' if report['independent_suite_ids'] else 'no'}",
        "",
    ]
    for round_ in report["rounds"]:
        a0 = round_["a0"]
        a1 = round_["a1"]
        lines.extend(
            [
                f"## Round {round_['round']}: {'PASS' if round_['passed'] else 'FAIL'}",
                "",
                f"- A0 suite: `{round_['a0_suite_id']}`; pass rate={a0['pass_rate']:.3f}; "
                f"prompt mean/max/n={a0['prompt_chars_mean']:.1f}/{a0['prompt_chars_max']}/{a0['prompt_char_samples']}",
                f"- A1 suite: `{round_['a1_suite_id']}`; pass rate={a1['pass_rate']:.3f}; "
                f"prompt mean/max/n={a1['prompt_chars_mean']:.1f}/{a1['prompt_chars_max']}/{a1['prompt_char_samples']}",
                f"- Prompt reduction: {round_['prompt_reduction_rate']:.2%}",
                f"- Retention: A0={_format_rate(a0['retention_rate'])}, A1={_format_rate(a1['retention_rate'])}",
                f"- Repeated I/O mean: A0={a0['repeated_io_mean']:.2f}, A1={a1['repeated_io_mean']:.2f}",
                f"- Cache: A0={a0['cache']['status']}, A1={a1['cache']['status']}",
                f"- A1 compaction overhead: prompt_tokens={a1['semantic_compaction_prompt_tokens']}, "
                f"completion_tokens={a1['semantic_compaction_completion_tokens']}, "
                f"latency_ms={a1['semantic_compaction_latency_ms']}",
            ]
        )
        for name, passed in round_["criteria"].items():
            lines.append(f"- {'PASS' if passed else 'FAIL'} `{name}`")
        for error in round_["comparability_errors"]:
            lines.append(f"- Comparison error: {error}")
        lines.append("")
    return "\n".join(lines)


def _variant_metrics(suite: dict[str, Any], *, semantic: bool) -> dict[str, Any]:
    cases = suite.get("cases", [])
    run_metrics = [case.get("metrics", {}) for case in cases if isinstance(case, dict)]
    runs = len(run_metrics)
    grouped_passes: dict[str, list[bool]] = {}
    for case in cases:
        if isinstance(case, dict):
            grouped_passes.setdefault(str(case.get("name")), []).append(bool(case.get("passed")))
    prompt_samples = sum(_integer(metrics.get("prompt_char_samples")) for metrics in run_metrics)
    prompt_total = sum(_integer(metrics.get("prompt_chars_total")) for metrics in run_metrics)
    expected = sum(_integer(metrics.get("context_retention_expected")) for metrics in run_metrics)
    retained = sum(_integer(metrics.get("context_retention_retained")) for metrics in run_metrics)
    observed_hit = sum(_integer(metrics.get("cache_observed_hit_tokens")) for metrics in run_metrics)
    observed_prompt = sum(_integer(metrics.get("cache_observed_prompt_tokens")) for metrics in run_metrics)
    cache_supported_runs = sum(bool(metrics.get("cache_metrics_available")) for metrics in run_metrics)
    if cache_supported_runs == 0:
        cache_status = "unsupported"
        cache_rate = None
    else:
        cache_status = "supported" if cache_supported_runs == runs else "partial"
        cache_rate = observed_hit / observed_prompt if observed_prompt else 0.0
    return {
        "runs": runs,
        "passed_runs": sum(bool(case.get("passed")) for case in cases if isinstance(case, dict)),
        "pass_rate": (
            sum(bool(case.get("passed")) for case in cases if isinstance(case, dict)) / runs if runs else 0.0
        ),
        "case_pass_rates": {
            name: sum(values) / len(values) for name, values in sorted(grouped_passes.items())
        },
        "all_runs_compacted": bool(runs)
        and all(
            _integer(metrics.get("context_compactions")) > 0
            and (
                not semantic
                or (
                    metrics.get("context_compaction_strategy") == "semantic"
                    and
                    _integer(metrics.get("semantic_compaction_successes")) > 0
                    and _integer(metrics.get("semantic_compaction_failures")) == 0
                )
            )
            for metrics in run_metrics
        ),
        "all_runs_triggered": bool(runs)
        and all(
            bool(metrics.get("context_budget_triggered"))
            and metrics.get("context_compaction_strategy") == "disabled"
            for metrics in run_metrics
        ),
        "prompt_chars_mean": prompt_total / prompt_samples if prompt_samples else 0.0,
        "prompt_chars_max": max((_integer(metrics.get("prompt_chars_max")) for metrics in run_metrics), default=0),
        "prompt_char_samples": prompt_samples,
        "retention_eligible": bool(runs)
        and all(_integer(metrics.get("context_retention_expected")) > 0 for metrics in run_metrics),
        "retention_rate": retained / expected if expected else None,
        "repeated_file_reads": sum(_integer(metrics.get("repeated_file_reads")) for metrics in run_metrics),
        "repeated_searches": sum(_integer(metrics.get("repeated_searches")) for metrics in run_metrics),
        "semantic_compaction_prompt_tokens": sum(
            _integer(metrics.get("semantic_compaction_prompt_tokens")) for metrics in run_metrics
        ),
        "semantic_compaction_completion_tokens": sum(
            _integer(metrics.get("semantic_compaction_completion_tokens")) for metrics in run_metrics
        ),
        "semantic_compaction_latency_ms": sum(
            _integer(metrics.get("semantic_compaction_latency_ms")) for metrics in run_metrics
        ),
        "repeated_io_mean": (
            sum(
                _integer(metrics.get("repeated_file_reads")) + _integer(metrics.get("repeated_searches"))
                for metrics in run_metrics
            )
            / runs
            if runs
            else 0.0
        ),
        "cache": {
            "status": cache_status,
            "supported_runs": cache_supported_runs,
            "runs": runs,
            "hit_tokens": observed_hit,
            "observed_prompt_tokens": observed_prompt,
            "weighted_hit_rate": cache_rate,
        },
    }


def _validate_variant(suite: dict[str, Any], expected: str) -> None:
    actual = (suite.get("configuration") or {}).get("context_variant")
    if actual != expected:
        raise ValueError(f"expected {expected} suite, got context_variant={actual!r}")


def _comparability_errors(a0: dict[str, Any], a1: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    a0_cases = [(case.get("name"), case.get("attempt")) for case in a0.get("cases", [])]
    a1_cases = [(case.get("name"), case.get("attempt")) for case in a1.get("cases", [])]
    if a0_cases != a1_cases:
        errors.append("case names or attempts differ")
    ignored = {"context_variant", "compaction_strategy"}
    a0_config = a0.get("configuration") or {}
    a1_config = a1.get("configuration") or {}
    for key in sorted((set(a0_config) | set(a1_config)) - ignored):
        if a0_config.get(key) != a1_config.get(key):
            errors.append(f"configuration differs for {key}")
    return errors


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_rate(value: float | None) -> str:
    return "unsupported" if value is None else f"{value:.2%}"
