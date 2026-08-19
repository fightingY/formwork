"""Validate and aggregate an immutable V3.5 fixed-regression suite report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return value


def _verifier_hash(case: dict[str, Any]) -> str:
    for assertion in case.get("assertion_specs", []):
        if isinstance(assertion, dict) and assertion.get("type") == "python_verifier":
            return str(assertion.get("sha256") or "")
    return ""


def _within_budget(case: dict[str, Any], manifest_case: dict[str, Any]) -> bool:
    if not case.get("passed"):
        return False
    budget = manifest_case.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}
    metrics = case.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    limits = {
        "turns": budget.get("max_turns"),
        "bash_actions": budget.get("max_bash_actions"),
        "action_timeout_sec": budget.get("max_action_timeout_sec"),
    }
    if limits["turns"] is not None and int(metrics.get("turns", 0)) > int(limits["turns"]):
        return False
    if limits["bash_actions"] is not None and int(metrics.get("bash_actions", 0)) > int(limits["bash_actions"]):
        return False
    if int(metrics.get("timeouts", 0) or 0) > 0:
        return False
    return True


def validate_input(report: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("suite report must contain a cases list")
    expected_cases = manifest.get("cases")
    order = manifest.get("execution_order")
    if not isinstance(expected_cases, dict) or not isinstance(order, list):
        raise ValueError("frozen manifest must contain cases and execution_order")
    expected_names = [str(name) for name in order]
    if len(expected_names) != 6 or set(expected_names) != set(expected_cases):
        raise ValueError("frozen manifest must contain exactly six unique cases")
    denominator = int(manifest.get("denominator", 0))
    repeat = int(manifest.get("repeat", 0))
    if denominator != 18 or repeat != 3:
        raise ValueError("V3.5 denominator must be 18 with repeat 3")
    if len(cases) != denominator:
        raise ValueError(f"expected exactly {denominator} case results, got {len(cases)}")
    configuration = report.get("configuration")
    if configuration is not None:
        if not isinstance(configuration, dict):
            raise ValueError("suite configuration must be an object")
        provider = manifest.get("provider", {})
        sandbox = manifest.get("sandbox", {})
        expected = {
            "model": provider.get("model") if isinstance(provider, dict) else None,
            "temperature": provider.get("temperature") if isinstance(provider, dict) else None,
            "sandbox_mode": sandbox.get("mode") if isinstance(sandbox, dict) else None,
        }
        for key, value in expected.items():
            if value is not None and configuration.get(key) != value:
                raise ValueError(f"suite configuration {key} does not match frozen manifest")
        if configuration.get("execute_local") is True:
            raise ValueError("formal benchmark cannot use execute_local")
    ids: set[str] = set()
    counts = {name: 0 for name in expected_names}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case result must be an object")
        name = str(case.get("name") or "")
        if name not in counts:
            raise ValueError(f"case is not in frozen manifest: {name}")
        run_id = str(case.get("run_id") or "")
        suite_id = str(case.get("suite_id") or "")
        if not run_id or run_id in ids:
            raise ValueError(f"run id is missing or duplicated: {run_id}")
        ids.add(run_id)
        if not suite_id:
            raise ValueError(f"suite id is missing for {run_id}")
        frozen = expected_cases[name]
        if not isinstance(frozen, dict):
            raise ValueError(f"manifest case is invalid: {name}")
        for result_key, manifest_key in (
            ("case_definition_sha256", "definition_sha256"),
            ("fixture_content_sha256", "fixture_sha256"),
        ):
            if str(case.get(result_key) or "") != str(frozen.get(manifest_key) or ""):
                raise ValueError(f"{name} {result_key} does not match frozen manifest")
        if _verifier_hash(case) != str(frozen.get("verifier_sha256") or ""):
            raise ValueError(f"{name} verifier hash does not match frozen manifest")
        counts[name] += 1
    if any(value != repeat for value in counts.values()):
        raise ValueError(f"each frozen case must occur exactly {repeat} times: {counts}")
    return cases


def aggregate(report: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    cases = validate_input(report, manifest)
    frozen = manifest["cases"]
    suite_budget = manifest.get("budget", {})
    if isinstance(suite_budget, dict):
        frozen = {
            name: (
                {**value, "budget": value.get("budget", suite_budget)}
                if isinstance(value, dict)
                else value
            )
            for name, value in frozen.items()
        }
    passed = sum(bool(case.get("passed")) for case in cases)
    budget_success = sum(_within_budget(case, frozen[str(case["name"])]) for case in cases)
    verdicts = {str(case.get("verdict") or "failed") for case in cases}
    verifier_failed = sum(str(case.get("verdict") or "") == "failed" for case in cases)
    timeouts = sum(str(case.get("verdict") or "") == "timeout" for case in cases)
    infrastructure_errors = sum(str(case.get("verdict") or "") == "infrastructure_error" for case in cases)
    return {
        "schema_version": 1,
        "suite_id": str(manifest.get("suite_id") or "v3.5-public-benchmark"),
        "milestone": "v3.5-public-benchmark",
        "status": "complete",
        "denominator": 18,
        "passed_runs": passed,
        "final_pass_rate": passed / 18,
        "budget_success_runs": budget_success,
        "budget_success_rate": budget_success / 18,
        "verifier_failed_runs": verifier_failed,
        "timeout_runs": timeouts,
        "infrastructure_error_runs": infrastructure_errors,
        "verdicts": sorted(verdicts),
        "cases": cases,
        "manifest": {
            "suite_id": manifest.get("suite_id"),
            "repeat": manifest.get("repeat"),
            "execution_order": manifest.get("execution_order"),
        },
    }


def write_aggregate(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"aggregator output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(
        "# V3.5 benchmark aggregate\n\n"
        f"Final pass rate: {result['passed_runs']}/{result['denominator']} "
        f"({result['final_pass_rate']:.3f})\n\n"
        f"Budget success rate: {result['budget_success_runs']}/{result['denominator']} "
        f"({result['budget_success_rate']:.3f})\n",
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(load_json(args.suite_report), load_manifest(args.manifest))
    print(f"aggregate: {write_aggregate(result, args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
