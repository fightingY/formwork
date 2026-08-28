import json

from minicc.core.protocol import PROTOCOL_SCHEMA_VERSION
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


def test_catalog_rejects_new_eval_entry_with_incomplete_evidence(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "run-incomplete"
    run_dir.mkdir(parents=True)
    result = type(
        "Result",
        (),
        {
            "run_id": "run-incomplete",
            "run_dir": str(run_dir),
            "run_status": "completed",
            "passed": True,
            "name": "C02",
            "attempt": 1,
            "suite_id": "suite-1",
            "milestone": "stable-v2.0.2",
            "stage": "formal_acceptance",
            "task_success": True,
            "agent_success": True,
            "infrastructure_success": True,
            "policy_outcome": "clear",
            "metrics": {},
        },
    )()

    entry = RunCatalog(tmp_path / ".minicc" / "versions").register_eval_result(
        "stable-v2.0.2",
        result,
        stage="formal_acceptance",
        suite_path=str(tmp_path / ".minicc" / "suites" / "suite-1" / "manifest.json"),
    )

    assert entry is None
    assert RunCatalog(tmp_path / ".minicc" / "versions").read_manifest("stable-v2.0.2")["entries"] == []


def test_catalog_v2_entry_links_complete_run_and_suite_without_dangling_pointer(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "run-complete"
    (run_dir / "artifacts").mkdir(parents=True)
    for relative in [
        "state.json",
        "trace.jsonl",
        "metrics.json",
        "workspace_manifest.json",
    ]:
        (run_dir / relative).write_text("{}", encoding="utf-8")
    (run_dir / "eval_result.json").write_text(
        json.dumps(
            {
                "schema_version": PROTOCOL_SCHEMA_VERSION,
                "run_id": "run-complete",
                "suite_id": "suite-1",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "artifacts" / "diff.patch").write_text("", encoding="utf-8")
    suite_path = tmp_path / ".minicc" / "suites" / "suite-1" / "manifest.json"
    suite_path.parent.mkdir(parents=True)
    suite_path.write_text(
        json.dumps({"schema_version": PROTOCOL_SCHEMA_VERSION, "suite_id": "suite-1"}),
        encoding="utf-8",
    )
    result = type(
        "Result",
        (),
        {
            "run_id": "run-complete",
            "run_dir": str(run_dir),
            "run_status": "completed",
            "passed": True,
            "name": "C02",
            "attempt": 1,
            "suite_id": "suite-1",
            "task_success": True,
            "agent_success": True,
            "infrastructure_success": True,
            "policy_outcome": "clear",
            "metrics": {},
        },
    )()

    catalog = RunCatalog(tmp_path / ".minicc" / "versions")
    entry = catalog.register_eval_result(
        "stable-v2.0.2",
        result,
        stage="formal_acceptance",
        suite_path=str(suite_path),
        report_path=str(suite_path.parent / "report.json"),
    )

    assert entry is not None
    assert entry["schema_version"] == PROTOCOL_SCHEMA_VERSION
    assert entry["suite_id"] == "suite-1"
    assert entry["suite_path"] == str(suite_path.resolve())
    assert entry["evidence_valid"] is True
    assert entry["formal_metric_eligible"] is True


def test_catalog_accepts_expected_hitl_waiting_state_for_formal_metrics(tmp_path) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / "run-hitl"
    (run_dir / "artifacts").mkdir(parents=True)
    for relative in ["state.json", "trace.jsonl", "metrics.json", "workspace_manifest.json"]:
        (run_dir / relative).write_text("{}", encoding="utf-8")
    (run_dir / "eval_result.json").write_text(
        json.dumps(
            {
                "schema_version": PROTOCOL_SCHEMA_VERSION,
                "run_id": "run-hitl",
                "suite_id": "suite-hitl",
                "passed": True,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "artifacts" / "diff.patch").write_text("", encoding="utf-8")
    suite_path = tmp_path / ".minicc" / "suites" / "suite-hitl" / "manifest.json"
    suite_path.parent.mkdir(parents=True)
    suite_path.write_text(
        json.dumps({"schema_version": PROTOCOL_SCHEMA_VERSION, "suite_id": "suite-hitl"}),
        encoding="utf-8",
    )
    result = type(
        "Result",
        (),
        {
            "run_id": "run-hitl",
            "run_dir": str(run_dir),
            "run_status": "waiting_approval",
            "passed": True,
            "name": "C09",
            "attempt": 1,
            "suite_id": "suite-hitl",
            "task_success": True,
            "agent_success": True,
            "infrastructure_success": True,
            "policy_outcome": "clear",
            "metrics": {},
        },
    )()

    entry = RunCatalog(tmp_path / ".minicc" / "versions").register_eval_result(
        "stable-v2.0.2",
        result,
        stage="formal_acceptance",
        suite_path=str(suite_path),
    )

    assert entry is not None
    assert entry["status"] == "waiting_approval"
    assert entry["formal_metric_eligible"] is True
