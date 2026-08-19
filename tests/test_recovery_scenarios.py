from __future__ import annotations

from pathlib import Path

import yaml

from tools.run_v35_recovery_matrix import run_matrix

ROOT = Path(__file__).parents[1]


def test_recovery_contract_has_eight_scenarios_and_nine_assertions() -> None:
    data = yaml.safe_load(
        (ROOT / "eval_cases/recovery_suite_v1/scenarios.yaml").read_text(encoding="utf-8")
    )
    assert len(data["scenarios"]) == 8
    assert sum(len(item["assertions"]) for item in data["scenarios"]) == 9
    assert {"wrong_run_id_rejected"} <= set(data["scenarios"][5]["assertions"])
    assert {"wrong_workspace_path_rejected"} <= set(data["scenarios"][6]["assertions"])


def test_recovery_matrix_is_deterministic_and_has_no_duplicate_actions() -> None:
    report = run_matrix()
    assert report["status"] == "PASS"
    assert report["scenario_count"] == 8
    assert report["assertion_count"] == 9
    assert report["passed_assertions"] == 9
    assert report["drift_detection_rate"] == 1.0
    assert report["duplicate_action_count"] == 0
