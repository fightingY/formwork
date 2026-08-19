from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import yaml

from minicc.evals.assertions import run_assertion
from minicc.evals.case import discover_cases, load_case
from minicc.sandbox.workspace import workspace_content_digest

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "eval_cases" / "public_benchmark_v1"


def test_public_suite_has_six_unique_frozen_cases_and_source_lock() -> None:
    cases = discover_cases(PUBLIC)
    assert [case.name for case in cases] == [
        "go-counting",
        "rest-api",
        "scale-generator",
        "simple-linked-list",
        "variable-length-quantity",
        "wordy",
    ]
    lock = yaml.safe_load((PUBLIC / "source_lock.yaml").read_text(encoding="utf-8"))
    assert lock["status"] == "frozen"
    assert len(lock["source"]["commit"]) == 40
    assert lock["source"]["dirty"] is False
    assert len(lock["tasks"]) == 6


def test_public_case_hashes_and_hidden_verifiers_are_bound() -> None:
    suite = yaml.safe_load((PUBLIC / "suite.yaml").read_text(encoding="utf-8"))
    for case in discover_cases(PUBLIC):
        frozen = suite["cases"][case.name]
        verifier = case.case_dir / "verifier" / "verify.py"
        assert case.definition_sha256 == frozen["definition_sha256"]
        assert workspace_content_digest(case.fixture_dir) == frozen["fixture_sha256"]
        assert hashlib.sha256(verifier.read_bytes()).hexdigest() == frozen["verifier_sha256"]
        assert not (case.fixture_dir / "verifier").exists()


def test_initial_python_verifier_fails_for_each_fixture(tmp_path: Path) -> None:
    for case in discover_cases(PUBLIC):
        workspace = tmp_path / case.name
        shutil.copytree(case.fixture_dir, workspace)
        result = run_assertion(
            {**case.initial_verify, "_artifact_label": "initial"},
            workspace_dir=workspace,
            run_dir=tmp_path / f"run-{case.name}",
            metrics={},
            verifier_dir=case.case_dir / "verifier",
        )
        assert result.passed, (case.name, result.message)


def test_python_verifier_initial_contract_rejects_unsafe_path(tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    path.write_text(
        """name: unsafe
prompt: test
fixture: fixture
initial_verify:
  type: python_verifier
  path: ../verify.py
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
""",
        encoding="utf-8",
    )
    (tmp_path / "fixture").mkdir()
    try:
        load_case(path)
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("unsafe verifier path was accepted")
