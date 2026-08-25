import json
import os
import shutil
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minicc.core import ledger
from minicc.core.ledger import (
    LEDGER_SCHEMA_VERSION,
    apply_cleanup_plan,
    build_cleanup_plan,
    inspect_run,
    new_suite_id,
    write_artifact_index,
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

    written_manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert written_manifest["suite_id"] == suite_id
    assert written_manifest["artifacts"]["report_json"]["path"] == "report.json"
    assert len(written_manifest["artifacts"]["report_json"]["sha256"]) == 64
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


def test_resumable_artifact_index_stays_idempotent_when_evidence_changes(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "run-resumable"
    run_dir.mkdir(parents=True)
    state_path = run_dir / "state.json"
    state_path.write_text('{"status":"waiting_approval"}', encoding="utf-8")
    evidence = {"state": str(state_path)}

    first = write_artifact_index(
        tmp_path / ".minicc" / "artifacts",
        run_id="run-resumable",
        run_dir=run_dir,
        evidence=evidence,
    )
    state_path.write_text('{"status":"completed"}', encoding="utf-8")
    second = write_artifact_index(
        tmp_path / ".minicc" / "artifacts",
        run_id="run-resumable",
        run_dir=run_dir,
        evidence=evidence,
    )

    assert second == first
    assert "artifacts" not in json.loads(first.read_text(encoding="utf-8"))


def test_inspect_run_marks_stale_running_as_orphaned_without_failing_task(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "run-stale"
    run_dir.mkdir(parents=True)
    started_at = datetime(2026, 7, 19, tzinfo=UTC)
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
        now=datetime(2026, 7, 20, tzinfo=UTC),
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


def test_inspect_run_accepts_only_verified_hitl_waiting_state_for_formal_metrics(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "run-hitl"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "run_id": "run-hitl",
                "suite_id": "suite-hitl",
                "status": "waiting_approval",
            }
        ),
        encoding="utf-8",
    )
    for relative in ["trace.jsonl", "metrics.json", "workspace_manifest.json"]:
        (run_dir / relative).write_text("{}", encoding="utf-8")
    (run_dir / "artifacts" / "diff.patch").write_text("", encoding="utf-8")
    result_path = run_dir / "eval_result.json"
    result = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "run_id": "run-hitl",
        "suite_id": "suite-hitl",
        "passed": True,
        "task_success": True,
        "agent_success": True,
        "infrastructure_success": True,
        "policy_outcome": "clear",
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")

    assert inspect_run(run_dir)["formal_metric_eligible"] is True

    result["passed"] = False
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert inspect_run(run_dir)["formal_metric_eligible"] is False


def test_cleanup_plan_and_apply_share_selection_and_protect_indexed_acceptance(tmp_path) -> None:
    runs_root = tmp_path / ".minicc" / "runs"
    versions_root = tmp_path / ".minicc" / "versions"
    acceptance_root = tmp_path / "acceptance"
    old_timestamp = datetime(2026, 7, 1, tzinfo=UTC).timestamp()
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
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    dry_run_ids = [candidate.run_id for candidate in plan.candidates]
    applied = apply_cleanup_plan(plan, apply=True)

    assert dry_run_ids == ["delete-me"]
    assert applied.deleted_run_ids == dry_run_ids
    assert not (runs_root / "delete-me").exists()
    assert (runs_root / "indexed-run").exists()
    assert (runs_root / "accepted-run").exists()


def test_cleanup_retries_readonly_files_without_hiding_other_errors(tmp_path, monkeypatch) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "delete-readonly"
    git_object = run_dir / "workspace" / ".git" / "objects" / "aa" / "object"
    git_object.parent.mkdir(parents=True)
    git_object.write_text("content", encoding="utf-8")
    os.chmod(git_object, stat.S_IREAD)
    plan = ledger.CleanupPlan(
        runs_root=runs_root,
        protected_run_ids=(),
        candidates=(
            ledger.CleanupCandidate(
                run_id=run_dir.name,
                run_dir=run_dir,
                reason="test",
            ),
        ),
    )
    real_rmtree = shutil.rmtree
    retried: list[Path] = []

    def fail_once_on_readonly(path, *, onerror=None):
        assert onerror is not None

        def remove(target: str) -> None:
            retried.append(Path(target))
            Path(target).unlink()

        try:
            raise PermissionError("read-only Git object")
        except PermissionError:
            onerror(remove, str(git_object), sys.exc_info())
        real_rmtree(path)

    monkeypatch.setattr(ledger.shutil, "rmtree", fail_once_on_readonly)

    result = apply_cleanup_plan(plan, apply=True)

    assert result.deleted_run_ids == ["delete-readonly"]
    assert retried == [git_object]
    assert not run_dir.exists()
