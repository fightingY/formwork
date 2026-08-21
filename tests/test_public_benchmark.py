from __future__ import annotations

import shutil
from pathlib import Path

from minicc.evals.assertions import run_assertion
from minicc.evals.case import discover_cases, load_case

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "eval_cases" / "public_benchmark_v1"


def test_public_suite_has_six_unique_cases() -> None:
    cases = discover_cases(PUBLIC)
    assert [case.name for case in cases] == [
        "go-counting",
        "rest-api",
        "scale-generator",
        "simple-linked-list",
        "variable-length-quantity",
        "wordy",
    ]


def test_public_case_hidden_verifiers_are_not_vendored() -> None:
    for case in discover_cases(PUBLIC):
        assert (case.case_dir / "verifier" / "verify.py").is_file(), case.name
        assert not (case.fixture_dir / "verifier").exists(), case.name


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