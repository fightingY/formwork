import json

from minicc.core.run_catalog import RunCatalog, index_acceptance_history
from minicc.core.state import RunState


def test_catalog_creates_human_readable_version_entry_without_moving_run(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    state = RunState.start("修复测试", run_dir=run_dir)
    state.run_id = "run-1"
    state.status = "completed"

    catalog = RunCatalog(tmp_path / ".minicc" / "versions")
    entry = catalog.register_state("stable-v2.1", state)

    assert entry is not None
    assert run_dir.exists()
    assert entry["title"] == "[V2.1][日常开发][RUN][PASS]"
    manifest = json.loads(
        (tmp_path / ".minicc" / "versions" / "stable-v2.1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["entry_count"] == 1
    entry_file = tmp_path / ".minicc" / "versions" / "stable-v2.1" / entry["entry_file"]
    assert entry_file.exists()
    assert entry_file.parent.name == "日常开发"


def test_catalog_updates_existing_entry_after_resume(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    state = RunState.start("恢复任务", run_dir=run_dir)
    state.run_id = "run-1"
    state.status = "interrupted"
    catalog = RunCatalog(tmp_path / ".minicc" / "versions")
    first = catalog.register_state("stable-v2.1", state, stage="checkpoint_resume")

    state.status = "completed"
    updated = catalog.update_existing_state(state)

    assert first is not None and updated is not None
    assert updated["result"] == "PASS"
    assert updated["stage"] == "checkpoint_resume"
    assert len(catalog.read_manifest("stable-v2.1")["entries"]) == 1
    assert not (tmp_path / ".minicc" / "versions" / "stable-v2.1" / first["entry_file"]).exists()


def test_index_acceptance_history_imports_report_entries(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "eval-C01-r1-20260716-120000-aaaaaaaa"
    run_dir.mkdir(parents=True)
    report_dir = tmp_path / "acceptance" / "stable-v1.3"
    report_dir.mkdir(parents=True)
    (report_dir / "eval_report.json").write_text(
        json.dumps(
            {
                "configuration": {"git_commit": "abc123"},
                "cases": [
                    {
                        "name": "C01_repo_onboarding",
                        "passed": True,
                        "run_status": "completed",
                        "attempt": 1,
                        "run_id": run_dir.name,
                        "run_dir": str(run_dir),
                        "metrics": {"started_at": "2026-07-16T12:00:00"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    counts = index_acceptance_history(tmp_path)
    catalog = RunCatalog(tmp_path / ".minicc" / "versions")
    entries = catalog.read_manifest("stable-v1.3")["entries"]

    assert counts["stable-v1.3"] == 1
    assert entries[0]["title"] == "[V1.3][正式验收][C01][第1轮][PASS]"
    assert entries[0]["git_commit"] == "abc123"
