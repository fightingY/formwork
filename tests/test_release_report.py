import csv
import json
from pathlib import Path

import pytest

from minicc.evals.release_report import (
    build_release_report,
    load_context_suite_evidence,
    load_json_evidence,
    write_release_report,
)


def test_release_report_covers_four_traceable_dimensions_and_writes_bundle(tmp_path) -> None:
    inputs = _inputs(tmp_path)

    report = build_release_report(**inputs, source_commit="abc123")

    assert report["passed"] is True
    assert [row["run_count"] for row in report["dimensions"]] == [15, 24, 27, 1]
    assert report["criteria"]["all_claims_traceable"] is True
    bundle = write_release_report(report, tmp_path / "release")
    assert {path.name for path in bundle.json_path.parent.iterdir()} == {
        "report.json",
        "report.md",
        "report.csv",
        "manifest.json",
    }
    with bundle.csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    assert {row["dimension"] for row in rows} == {
        "system_regression",
        "context_governance",
        "memory_benefit",
        "checkpoint_resume",
    }


def test_release_report_fails_closed_when_a_claim_loses_run_ids(tmp_path) -> None:
    inputs = _inputs(tmp_path)
    inputs["resume_report"]["real_model_resume"]["run_id"] = ""

    report = build_release_report(**inputs, source_commit="abc123")

    assert report["passed"] is False
    assert report["criteria"]["all_dimensions_stable_and_passed"] is False
    bundle = write_release_report(report, tmp_path / "release")
    written = json.loads(bundle.json_path.read_text(encoding="utf-8"))
    assert written["status"] == "FAIL"


def test_release_report_renders_missing_dimensions_as_experimental_empty(tmp_path) -> None:
    report = build_release_report(
        system_report={},
        context_report={},
        context_suites=[],
        memory_report={},
        resume_report={},
        source_commit="abc123",
    )

    assert report["passed"] is False
    assert {row["state"] for row in report["dimensions"]} == {"experimental"}
    assert {row["result"] for row in report["dimensions"]} == {"EMPTY"}
    bundle = write_release_report(report, tmp_path / "release")
    assert bundle.markdown_path.is_file()


def test_context_suite_loader_requires_all_four_matching_pass_suites(tmp_path) -> None:
    context_report = _context_report(tmp_path)
    suites_root = tmp_path / "suites"
    for index, suite_id in enumerate(("a0-r1", "a1-r1", "a0-r2", "a1-r2"), start=1):
        suite_dir = suites_root / suite_id
        suite_dir.mkdir(parents=True)
        (suite_dir / "report.json").write_text(
            json.dumps(
                {
                    "suite_id": suite_id,
                    "passed": True,
                    "cases": [{"run_id": f"run-{index}"}],
                }
            ),
            encoding="utf-8",
        )

    suites = load_context_suite_evidence(context_report, suites_root=suites_root)

    assert [suite["suite_id"] for suite in suites] == ["a0-r1", "a1-r1", "a0-r2", "a1-r2"]
    (suites_root / "a1-r2" / "report.json").write_text(
        json.dumps({"suite_id": "wrong", "passed": True}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="matching PASS"):
        load_context_suite_evidence(context_report, suites_root=suites_root)


def test_json_evidence_loader_hashes_the_exact_source(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text('{"passed": true}\n', encoding="utf-8")

    report = load_json_evidence(path)

    assert report["passed"] is True
    assert report["_source"]["path"] == str(path.resolve())
    assert len(report["_source"]["sha256"]) == 64


def _inputs(tmp_path: Path) -> dict:
    return {
        "system_report": _system_report(tmp_path),
        "context_report": _context_report(tmp_path),
        "context_suites": _context_suites(tmp_path),
        "memory_report": _memory_report(tmp_path),
        "resume_report": _resume_report(tmp_path),
    }


def _source(tmp_path: Path, name: str) -> dict:
    return {"path": str((tmp_path / name).resolve()), "bytes": 10, "sha256": name[0] * 64}


def _system_report(tmp_path: Path) -> dict:
    names = [
        "C01_repo_onboarding",
        "C02_fix_failing_test",
        "C03_add_cli_option",
        "C04_add_regression_test",
        "C09_hitl_destructive_command",
    ]
    return {
        "passed": True,
        "configuration": {"model": "provider/model"},
        "cases": [
            {
                "name": name,
                "run_id": f"{name}-r{attempt}",
                "run_dir": str(tmp_path / "runs" / f"{name}-r{attempt}"),
                "passed": True,
            }
            for attempt in range(1, 4)
            for name in names
        ],
        "_source": _source(tmp_path, "system.json"),
    }


def _context_report(tmp_path: Path) -> dict:
    return {
        "passed": True,
        "rounds": [
            {
                "a0_suite_id": "a0-r1",
                "a1_suite_id": "a1-r1",
                "prompt_reduction_rate": 0.1,
                "passed": True,
            },
            {
                "a0_suite_id": "a0-r2",
                "a1_suite_id": "a1-r2",
                "prompt_reduction_rate": 0.4,
                "passed": True,
            },
        ],
        "_source": _source(tmp_path, "context.json"),
    }


def _context_suites(tmp_path: Path) -> list[dict]:
    suites = []
    for suite_id, count in (("a0-r1", 3), ("a1-r1", 3), ("a0-r2", 9), ("a1-r2", 9)):
        suites.append(
            {
                "suite_id": suite_id,
                "passed": True,
                "cases": [
                    {"name": f"context-case-{index % 3}", "run_id": f"{suite_id}-run-{index}"}
                    for index in range(count)
                ],
                "_source": _source(tmp_path, f"{suite_id}.json"),
            }
        )
    return suites


def _memory_report(tmp_path: Path) -> dict:
    cases = []
    for case_index in range(1, 4):
        case_name = f"M0{case_index}_follow_up"
        attempts = []
        for attempt in range(1, 4):
            attempts.append(
                {
                    "source": {"run_id": f"{case_name}-source-{attempt}"},
                    "m0": {"run_id": f"{case_name}-m0-{attempt}"},
                    "m1": {"run_id": f"{case_name}-m1-{attempt}"},
                }
            )
        cases.append({"case_name": case_name, "suite_id": f"memory-suite-{case_index}", "attempts": attempts})
    return {
        "passed": True,
        "locked_configuration": {"model": "provider/model"},
        "aggregate": {
            "m0_repeated_source_file_reads": 9,
            "m1_repeated_source_file_reads": 0,
            "prompt_token_reduction_rate": 0.28,
        },
        "sources": [{"path": str(tmp_path / f"memory-suite-{index}.json")} for index in range(1, 4)],
        "cases": cases,
        "_source": _source(tmp_path, "memory.json"),
    }


def _resume_report(tmp_path: Path) -> dict:
    return {
        "result": "PASS",
        "git_commit": "resume-commit",
        "real_model_resume": {
            "result": "PASS",
            "run_id": "resume-run-1",
            "resume_count": 1,
            "duplicate_executions": 0,
            "workspace_verified": True,
            "trajectory_verified": True,
            "diff_verified": True,
            "checkpoint_restored": "checkpoint-4",
        },
        "_source": _source(tmp_path, "resume.json"),
    }
