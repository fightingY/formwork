from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from minicc.core.state import RunState
from minicc.evals.assertions import AssertionResult, run_assertions
from minicc.evals.case import EvalCase, discover_cases
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff


AgentRunCallable = Callable[[EvalCase, RunState], RunState]


@dataclass(frozen=True)
class EvalCaseResult:
    name: str
    capability: str
    passed: bool
    run_id: str
    run_dir: str
    assertions: list[AssertionResult]
    metrics: dict
    proves: str = ""


@dataclass(frozen=True)
class EvalSuiteResult:
    passed: bool
    cases: list[EvalCaseResult]


def run_eval_suite(
    path: Path,
    *,
    runs_root: Path,
    agent_runner: AgentRunCallable,
) -> EvalSuiteResult:
    cases = discover_cases(path)
    results = [run_eval_case(case, runs_root=runs_root, agent_runner=agent_runner) for case in cases]
    return EvalSuiteResult(passed=all(result.passed for result in results), cases=results)


def run_eval_case(
    case: EvalCase,
    *,
    runs_root: Path,
    agent_runner: AgentRunCallable,
) -> EvalCaseResult:
    clean_eval_run(runs_root, case.name)
    workspace = prepare_run_workspace(case.fixture_dir, run_id=f"eval-{case.name}", runs_root=runs_root)
    state = RunState.start(
        case.prompt,
        workspace_host_path=workspace.workspace_dir,
        run_dir=workspace.run_dir,
        artifacts_dir=workspace.artifacts_dir,
    )
    state.run_id = workspace.run_id
    state = agent_runner(case, state)
    write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir)
    metrics = _load_metrics(workspace.run_dir)
    assertion_results = run_assertions(
        case.assertions,
        workspace_dir=workspace.workspace_dir,
        run_dir=workspace.run_dir,
        metrics=metrics or state.metrics,
    )
    passed = state.status != "failed" and all(result.passed for result in assertion_results)
    return EvalCaseResult(
        name=case.name,
        capability=case.capability,
        passed=passed,
        run_id=state.run_id,
        run_dir=str(workspace.run_dir),
        assertions=assertion_results,
        metrics=metrics or state.metrics,
        proves=case.proves,
    )


def write_eval_report(result: EvalSuiteResult, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "eval_report.json"
    markdown_path = output_dir / "eval_report.md"
    json_path.write_text(json.dumps(suite_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(format_markdown_report(result), encoding="utf-8")
    return json_path, markdown_path


def suite_to_dict(result: EvalSuiteResult) -> dict:
    return {
        "passed": result.passed,
        "cases": [
            {
                "name": case.name,
                "capability": case.capability,
                "passed": case.passed,
                "run_id": case.run_id,
                "run_dir": case.run_dir,
                "proves": case.proves,
                "metrics": case.metrics,
                "assertions": [asdict(assertion) for assertion in case.assertions],
            }
            for case in result.cases
        ],
    }


def format_markdown_report(result: EvalSuiteResult) -> str:
    lines = ["# miniCC eval report", "", f"Overall: {'PASS' if result.passed else 'FAIL'}", ""]
    for case in result.cases:
        label = case.capability or case.name
        lines.append(f"## {label}: {'PASS' if case.passed else 'FAIL'}")
        if case.proves:
            lines.append(case.proves)
        lines.append(f"Run: `{case.run_id}`")
        lines.append(
            "Metrics: "
            f"turns={case.metrics.get('turns', 0)}, "
            f"bash_actions={case.metrics.get('bash_actions', 0)}, "
            f"policy_denials={case.metrics.get('policy_denials', 0)}"
        )
        for assertion in case.assertions:
            lines.append(f"- {'PASS' if assertion.passed else 'FAIL'} {assertion.type}: {assertion.message}")
        lines.append("")
    return "\n".join(lines)


def copy_report_to_run_root(result: EvalSuiteResult, runs_root: Path) -> tuple[Path, Path]:
    reports_dir = runs_root / "eval_reports"
    json_path, markdown_path = write_eval_report(result, reports_dir)
    return json_path, markdown_path


def _load_metrics(run_dir: Path) -> dict | None:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def clean_eval_run(runs_root: Path, name: str) -> None:
    target = runs_root / f"eval-{name}"
    if target.exists():
        shutil.rmtree(target, onerror=_make_writable_and_retry)


def _make_writable_and_retry(function: Callable, path: str, excinfo: tuple) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except Exception:
        exc_type, exc, traceback = excinfo
        raise exc.with_traceback(traceback)
