import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from minicc.core.ledger import (
    LEDGER_SCHEMA_VERSION,
    apply_cleanup_plan,
    build_cleanup_plan,
    inspect_run,
    new_suite_id,
    write_immutable_suite,
)


def test_immutable_suite_bundle_refuses_report_overwrite(tmp_path) -> None:
    suites_root = tmp_path / ".minicc" / "suites"
    suite_id = new_suite_id()
    manifest = {"schema_version": LEDGER_SCHEMA_VERSION, "suite_id": suite_id, "runs": []}
    report = {"schema_version": LEDGER_SCHEMA_VERSION, "suite_id": suite_id, "passed": True}

    bundle = write_immutable_suite(
        suites_root,
        suite_id=suite_id,
        manifest=manifest,
        report=report,
        markdown="# report\n",
        csv_text="run_id,result\n",
    )

    assert json.loads(bundle.manifest_path.read_text(encoding="utf-8"))["suite_id"] == suite_id
    assert bundle.report_json_path.name == "report.json"
    assert bundle.report_markdown_path.name == "report.md"
    assert bundle.report_csv_path.name == "report.csv"
    with pytest.raises(FileExistsError, match="immutable"):
        write_immutable_suite(
            suites_root,
            suite_id=suite_id,
            manifest=manifest,
            report=report,
            markdown="# overwritten\n",
            csv_text="run_id,result\n",
        )


def test_inspect_run_marks_stale_running_as_orphaned_without_failing_task(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "run-stale"
    run_dir.mkdir(parents=True)
    started_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "run_id": "run-stale",
                "goal": "unfinished",
                "status": "running",
                "metrics": {"started_at": started_at.isoformat()},
            }
        ),
        encoding="utf-8",
    )

    record = inspect_run(
        run_dir,
        now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        orphan_after=timedelta(hours=1),
    )

    assert record["status"] == "orphaned"
    assert record["result"] == "UNKNOWN"
    assert record["task_success"] is None
    assert record["agent_success"] is None
    assert record["infrastructure_success"] is None
    assert record["policy_outcome"] == "unknown"


def test_inspect_legacy_run_uses_unknown_instead_of_false(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "legacy-run"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        '{"run_id":"legacy-run","goal":"old","status":"completed"}',
        encoding="utf-8",
    )

    record = inspect_run(run_dir)

    assert record["schema_version"] == 1
    assert record["schema_semantics"] == "legacy/unknown"
    assert record["task_success"] is None
    assert record["agent_success"] is None
    assert record["infrastructure_success"] is None
    assert record["formal_metric_eligible"] is False


def test_cleanup_plan_and_apply_share_selection_and_protect_indexed_acceptance(tmp_path) -> None:
    runs_root = tmp_path / ".minicc" / "runs"
    versions_root = tmp_path / ".minicc" / "versions"
    acceptance_root = tmp_path / "acceptance"
    old_timestamp = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
    for run_id in ["delete-me", "indexed-run", "accepted-run"]:
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(
            json.dumps({"run_id": run_id, "goal": "x", "status": "failed"}),
            encoding="utf-8",
        )
        os.utime(run_dir, (old_timestamp, old_timestamp))

    version_dir = versions_root / "stable-v2.0.2"
    version_dir.mkdir(parents=True)
    (version_dir / "manifest.json").write_text(
        json.dumps({"entries": [{"run_id": "indexed-run"}]}),
        encoding="utf-8",
    )
    acceptance_root.mkdir()
    (acceptance_root / "eval_report.json").write_text(
        json.dumps({"cases": [{"run_id": "accepted-run"}]}),
        encoding="utf-8",
    )

    plan = build_cleanup_plan(
        runs_root,
        versions_root=versions_root,
        acceptance_root=acceptance_root,
        older_than=timedelta(days=1),
        now=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    dry_run_ids = [candidate.run_id for candidate in plan.candidates]
    applied = apply_cleanup_plan(plan, apply=True)

    assert dry_run_ids == ["delete-me"]
    assert applied.deleted_run_ids == dry_run_ids
    assert not (runs_root / "delete-me").exists()
    assert (runs_root / "indexed-run").exists()
    assert (runs_root / "accepted-run").exists()
