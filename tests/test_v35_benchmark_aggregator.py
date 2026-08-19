from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.aggregate_v35_benchmark import aggregate

ROOT = Path(__file__).parents[1]


def _report() -> tuple[dict, dict]:
    manifest = yaml.safe_load(
        (ROOT / "eval_cases/public_benchmark_v1/suite.yaml").read_text(encoding="utf-8")
    )
    rows = []
    for name in manifest["execution_order"]:
        frozen = manifest["cases"][name]
        for attempt in range(1, 4):
            rows.append(
                {
                    "name": name,
                    "run_id": f"run-{name}-{attempt}",
                    "suite_id": "suite-test",
                    "passed": True,
                    "verdict": "passed",
                    "case_definition_sha256": frozen["definition_sha256"],
                    "fixture_content_sha256": frozen["fixture_sha256"],
                    "assertion_specs": [{"type": "python_verifier", "sha256": frozen["verifier_sha256"]}],
                    "metrics": {"turns": 2, "bash_actions": 3, "timeouts": 0},
                }
            )
    return {
        "cases": rows,
        "configuration": {
            "model": "deepseek-v4-flash",
            "temperature": 0,
            "sandbox_mode": "locked",
            "execute_local": False,
        },
    }, manifest


def test_aggregator_recomputes_eighteen_run_denominator() -> None:
    result = aggregate(*_report())
    assert result["denominator"] == 18
    assert result["passed_runs"] == 18
    assert result["final_pass_rate"] == 1.0


@pytest.mark.parametrize("mutation", ["drop", "duplicate", "hash"])
def test_aggregator_rejects_invalid_frozen_input(mutation: str) -> None:
    report, manifest = _report()
    if mutation == "drop":
        report["cases"].pop()
    elif mutation == "duplicate":
        report["cases"][1]["run_id"] = report["cases"][0]["run_id"]
    else:
        report["cases"][0]["fixture_content_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        aggregate(report, manifest)
