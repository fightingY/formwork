from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from minicc.core.state import RunState, new_run_id
from minicc.evals.assertions import AssertionResult, run_assertions
from minicc.evals.case import EvalCase, discover_cases
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff


AgentRunCallable = Callable[[EvalCase, RunState], RunState]


@dataclass(frozen=True)
class EvalCaseResult:
    name: str
    capability: str
    passed: bool
    run_status: str
    run_id: str
    run_dir: str
    assertions: list[AssertionResult]
    metrics: dict
    attempt: int = 1
    sandbox_mode: str = "locked"
    budget: dict | None = None
    proves: str = ""


@dataclass(frozen=True)
class EvalSuiteResult:
    passed: bool
    cases: list[EvalCaseResult]
    repeat: int = 1
    configuration: dict | None = None


def run_eval_suite(
    path: Path,
    *,
    runs_root: Path,
    agent_runner: AgentRunCallable,
    repeat: int = 1,
    configuration: dict | None = None,
    preserve_runs: bool = False,
    case_names: list[str] | None = None,
) -> EvalSuiteResult:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    cases = discover_cases(path)
    if case_names:
        requested = set(case_names)
        discovered = {case.name for case in cases}
        missing = sorted(requested - discovered)
        if missing:
            raise ValueError(f"Unknown eval case(s): {', '.join(missing)}")
        cases = [case for case in cases if case.name in requested]
    results = [
        run_eval_case(
            case,
            runs_root=runs_root,
            agent_runner=agent_runner,
            attempt=attempt,
            preserve_run=preserve_runs or repeat > 1,
        )
        for attempt in range(1, repeat + 1)
        for case in cases
    ]
    return EvalSuiteResult(
        passed=all(result.passed for result in results),
        cases=results,
        repeat=repeat,
        configuration=configuration,
    )


def run_eval_case(
    case: EvalCase,
    *,
    runs_root: Path,
    agent_runner: AgentRunCallable,
    attempt: int = 1,
    preserve_run: bool = False,
) -> EvalCaseResult:
    if preserve_run:
        run_id = f"eval-{case.name}-r{attempt}-{new_run_id()}"
    else:
        clean_eval_run(runs_root, case.name)
        run_id = f"eval-{case.name}"
    workspace = prepare_run_workspace(case.fixture_dir, run_id=run_id, runs_root=runs_root)
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
    expected_status = _expected_run_status(case)
    status_passed = state.status == expected_status
    passed = status_passed and all(result.passed for result in assertion_results)
    result = EvalCaseResult(
        name=case.name,
        capability=case.capability,
        passed=passed,
        run_status=state.status,
        run_id=state.run_id,
        run_dir=str(workspace.run_dir),
        assertions=assertion_results,
        metrics=metrics or state.metrics,
        attempt=attempt,
        sandbox_mode=case.sandbox_mode,
        budget=dict(case.budget),
        proves=case.proves,
    )
    write_eval_case_report(result, workspace.run_dir)
    return result


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
        "repeat": result.repeat,
        "configuration": result.configuration or {},
        "case_summary": aggregate_case_results(result.cases),
        "cases": [
            {
                "name": case.name,
                "capability": case.capability,
                "passed": case.passed,
                "run_status": case.run_status,
                "attempt": case.attempt,
                "run_id": case.run_id,
                "run_dir": case.run_dir,
                "sandbox_mode": case.sandbox_mode,
                "budget": case.budget or {},
                "proves": case.proves,
                "metrics": case.metrics,
                "assertions": [asdict(assertion) for assertion in case.assertions],
            }
            for case in result.cases
        ],
    }


def format_markdown_report(result: EvalSuiteResult) -> str:
    lines = [
        "# miniCC eval report",
        "",
        f"Overall: {'PASS' if result.passed else 'FAIL'}",
        f"Repeat: {result.repeat}",
        "",
    ]
    if result.configuration:
        lines.extend(["## Configuration", ""])
        for name, value in result.configuration.items():
            lines.append(f"- {name}: `{value}`")
        lines.append("")
    lines.extend(["## Case Summary", ""])
    for summary in aggregate_case_results(result.cases):
        lines.append(
            f"- {summary['name']}: {summary['passed_runs']}/{summary['attempts']} passed "
            f"(pass_rate={summary['pass_rate']:.3f}), "
            f"avg_turns={summary['average_turns']:.2f}, "
            f"avg_bash_actions={summary['average_bash_actions']:.2f}, "
            f"avg_duration_ms={summary['average_duration_ms']:.0f}, "
            f"diff_paths={summary['diff_paths']}"
        )
    lines.append("")
    for case in result.cases:
        label = case.capability or case.name
        lines.append(f"## {label} attempt {case.attempt}: {'PASS' if case.passed else 'FAIL'}")
        if case.proves:
            lines.append(case.proves)
        lines.append(f"Run: `{case.run_id}`")
        lines.append(
            "Metrics: "
            f"turns={case.metrics.get('turns', 0)}, "
            f"bash_actions={case.metrics.get('bash_actions', 0)}, "
            f"policy_denials={case.metrics.get('policy_denials', 0)}, "
            f"duration_ms={case.metrics.get('total_duration_ms', 0)}"
        )
        for assertion in case.assertions:
            lines.append(f"- {'PASS' if assertion.passed else 'FAIL'} {assertion.type}: {assertion.message}")
        lines.append("")
    return "\n".join(lines)


def aggregate_case_results(cases: list[EvalCaseResult]) -> list[dict]:
    grouped: dict[str, list[EvalCaseResult]] = {}
    for case in cases:
        grouped.setdefault(case.name, []).append(case)

    summaries = []
    for name, results in sorted(grouped.items()):
        diff_paths = sorted({path for result in results for path in _changed_paths(result.run_dir)})
        failed_assertions = [
            f"attempt {result.attempt} {assertion.type}: {assertion.message}"
            for result in results
            for assertion in result.assertions
            if not assertion.passed
        ]
        for result in results:
            if not result.passed and not any(not assertion.passed for assertion in result.assertions):
                failed_assertions.append(
                    f"attempt {result.attempt} run_status: unexpected status {result.run_status}"
                )
        summaries.append(
            {
                "name": name,
                "capability": results[0].capability,
                "attempts": len(results),
                "passed_runs": sum(result.passed for result in results),
                "pass_rate": sum(result.passed for result in results) / len(results),
                "statuses": sorted({result.run_status for result in results}),
                "average_turns": sum(result.metrics.get("turns", 0) for result in results) / len(results),
                "average_bash_actions": sum(
                    result.metrics.get("bash_actions", 0) for result in results
                )
                / len(results),
                "average_duration_ms": sum(
                    result.metrics.get("total_duration_ms", 0) for result in results
                )
                / len(results),
                "diff_paths": diff_paths,
                "failure_reasons": failed_assertions,
            }
        )
    return summaries


def _changed_paths(run_dir: str) -> list[str]:
    diff_path = Path(run_dir) / "artifacts" / "diff.patch"
    if not diff_path.exists():
        return []
    paths = []
    for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = shlex.split(line)
        if len(parts) >= 4 and parts[3].startswith("b/"):
            paths.append(parts[3].removeprefix("b/"))
    return paths


def write_eval_case_report(result: EvalCaseResult, run_dir: Path) -> tuple[Path, Path]:
    payload = {
        "name": result.name,
        "capability": result.capability,
        "attempt": result.attempt,
        "passed": result.passed,
        "run_id": result.run_id,
        "run_status": result.run_status,
        "sandbox_mode": result.sandbox_mode,
        "budget": result.budget or {},
        "metrics": result.metrics,
        "assertions": [asdict(assertion) for assertion in result.assertions],
    }
    json_path = run_dir / "eval_result.json"
    markdown_path = run_dir / "eval_result.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# {result.name} attempt {result.attempt}",
        "",
        f"- Passed: `{'true' if result.passed else 'false'}`",
        f"- Run status: `{payload['run_status']}`",
        f"- Run id: `{result.run_id}`",
        f"- Sandbox mode: `{result.sandbox_mode}`",
        "",
        "## Verifier",
        "",
    ]
    for assertion in result.assertions:
        lines.append(f"- {'PASS' if assertion.passed else 'FAIL'} {assertion.type}: {assertion.message}")
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def copy_report_to_run_root(result: EvalSuiteResult, runs_root: Path) -> tuple[Path, Path]:
    reports_dir = runs_root / "eval_reports"
    json_path, markdown_path = write_eval_report(result, reports_dir)
    return json_path, markdown_path


def _load_metrics(run_dir: Path) -> dict | None:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def _expected_run_status(case: EvalCase) -> str:
    for assertion in case.assertions:
        if assertion.get("type") == "run_status":
            expected = str(assertion.get("value", "completed"))
            if expected == "waiting_approval" and case.capability.startswith("hitl"):
                return expected
    return "completed"


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
