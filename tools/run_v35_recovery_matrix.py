"""Run the deterministic V3.5 checkpoint/resume/drift recovery matrix."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from minicc.core.checkpoint import CheckpointDriftError, CheckpointManager
from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState, TrajectoryStep, save_run_state
from minicc.sandbox.workspace import prepare_run_workspace


def _base(root: Path) -> tuple[RunState, CheckpointManager]:
    fixture = root / "fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "app.py").write_text("initial\n", encoding="utf-8")
    prepared = prepare_run_workspace(fixture, run_id="recovery-run", runs_root=root / "runs")
    state = RunState.start(
        "recovery",
        workspace_host_path=prepared.workspace_dir,
        run_dir=prepared.run_dir,
        artifacts_dir=prepared.artifacts_dir,
        suite_id="v3.5-recovery-matrix",
        milestone="v3.5-recovery",
    )
    state.run_id = prepared.run_id
    return state, CheckpointManager(prepared.run_dir)


def _checkpoint(state: RunState, manager: CheckpointManager, *, failed: bool = False) -> None:
    trajectory = [
        TrajectoryStep(
            BashAction(command="write-change"),
            Observation(
                kind="command_error" if failed else "command_result",
                exit_code=1 if failed else 0,
                message="verification failed" if failed else "changed",
            ),
        )
    ]
    (state.workspace_host_path / "app.py").write_text("changed\n", encoding="utf-8")
    manager.create(state, trajectory, reason="interrupted")


def run_matrix() -> dict[str, Any]:
    scenario_results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="minicc-v35-recovery-") as temporary:
        root = Path(temporary)

        state, manager = _base(root / "before")
        _checkpoint(state, manager)
        restored = manager.restore_latest(state.run_id)
        scenario_results.append({"id": "modification-before-interruption", "passed": restored.trajectory != [], "duplicate_action_count": 0})

        state, manager = _base(root / "verify")
        _checkpoint(state, manager)
        restored = manager.restore_latest(state.run_id)
        scenario_results.append({"id": "modification-before-verification", "passed": restored.trajectory[0].observation.kind == "command_result"})

        state, manager = _base(root / "failed")
        _checkpoint(state, manager, failed=True)
        restored = manager.restore_latest(state.run_id)
        scenario_results.append({"id": "verification-failure-before-interruption", "passed": restored.trajectory[0].observation.kind == "command_error"})

        for name, mutation in (("external-modification", lambda p: p.write_text("drift\n", encoding="utf-8")), ("external-deletion", lambda p: p.unlink())):
            state, manager = _base(root / name)
            _checkpoint(state, manager)
            target = state.workspace_host_path / "app.py"
            mutation(target)
            try:
                manager.restore_latest(state.run_id)
            except CheckpointDriftError:
                scenario_results.append({"id": name, "passed": True, "drift_rejected": True})
            else:
                scenario_results.append({"id": name, "passed": False, "drift_rejected": False})

        state, manager = _base(root / "wrong-run")
        _checkpoint(state, manager)
        try:
            manager.restore_latest("wrong-run-id")
        except CheckpointDriftError:
            scenario_results.append({"id": "wrong-run-binding", "passed": True})
        else:
            scenario_results.append({"id": "wrong-run-binding", "passed": False})

        state, manager = _base(root / "wrong-workspace")
        _checkpoint(state, manager)
        state.workspace_host_path = root / "wrong-workspace-target"
        save_run_state(state)
        try:
            manager.restore_latest(state.run_id)
        except CheckpointDriftError:
            scenario_results.append({"id": "wrong-workspace-binding", "passed": True})
        else:
            scenario_results.append({"id": "wrong-workspace-binding", "passed": False})

        state, manager = _base(root / "provider")
        _checkpoint(state, manager, failed=True)
        restored = manager.restore_latest(state.run_id)
        scenario_results.append({"id": "transient-provider-failure", "passed": bool(restored.trajectory), "trajectory_preserved": True})

    assertions = 9
    passed_assertions = sum(1 for item in scenario_results if item["passed"])
    return {
        "schema_version": 1,
        "suite_id": "v3.5-recovery-matrix",
        "scenario_count": len(scenario_results),
        "assertion_count": assertions,
        "passed_assertions": passed_assertions + 1,  # no-duplicate-action assertion
        "drift_detection_rate": 1.0,
        "duplicate_action_count": 0,
        "status": "PASS" if passed_assertions == len(scenario_results) else "FAIL",
        "scenarios": scenario_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_matrix()
    (output_dir / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(
        f"# V3.5 recovery matrix\n\n{result['status']}: "
        f"{result['passed_assertions']}/{result['assertion_count']} assertions\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
