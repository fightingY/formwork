from __future__ import annotations

import csv
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from minicc.core.ledger import (
    LEDGER_SCHEMA_VERSION,
    SuiteBundle,
    new_suite_id,
    write_artifact_index,
    write_immutable_suite,
)
from minicc.core.state import RunState, new_run_id, save_run_state
from minicc.evals.assertions import (
    AssertionResult,
    run_assertion,
    run_assertions,
    trace_action_shape_evidence_events,
)
from minicc.evals.case import (
    EvalCase,
    case_source_path,
    discover_cases,
    fixture_source_path,
)
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff

AgentRunCallable = Callable[[EvalCase, RunState], RunState]
EvalCaseCompletedCallable = Callable[["EvalCaseResult"], None]


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
    task_success: bool = False
    agent_success: bool = False
    infrastructure_success: bool = False
    policy_outcome: str = "unknown"
    suite_id: str = ""
    milestone: str = ""
    stage: str = "development_precheck"
    configuration: dict | None = None
    case_source_path: str = ""
    fixture_source_path: str = ""
    case_definition_sha256: str = ""
    fixture_content_sha256: str = ""
    request_rows: list[dict] = field(default_factory=list)
    trace_assertion_events: list[dict] = field(default_factory=list)
    assertion_specs: list[dict] = field(default_factory=list)
    initial_verification: AssertionResult | None = None
    workspace_cleaned: bool = False
    cleanup_error: str = ""
    verdict: str = "failed"


@dataclass(frozen=True)
class EvalSuiteResult:
    passed: bool
    cases: list[EvalCaseResult]
    repeat: int = 1
    configuration: dict | None = None
    suite_id: str = ""
    milestone: str = ""
    stage: str = "development_precheck"
    created_at: str = ""


def run_eval_suite(
    path: Path,
    *,
    runs_root: Path,
    agent_runner: AgentRunCallable,
    repeat: int = 1,
    configuration: dict | None = None,
    preserve_runs: bool = False,
    case_names: list[str] | None = None,
    on_case_completed: EvalCaseCompletedCallable | None = None,
    suite_id: str | None = None,
    milestone: str = "",
    stage: str = "development_precheck",
) -> EvalSuiteResult:
    del preserve_runs  # Kept as a source-compatible no-op; V2.0.2 always preserves runs.
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    suite_id = suite_id or new_suite_id()
    created_at = datetime.now(UTC).isoformat()
    configuration_snapshot = dict(configuration or {})
    cases = discover_cases(path)
    if case_names:
        requested = set(case_names)
        discovered = {case.name for case in cases}
        missing = sorted(requested - discovered)
        if missing:
            raise ValueError(f"Unknown eval case(s): {', '.join(missing)}")
        cases = [case for case in cases if case.name in requested]
    results: list[EvalCaseResult] = []
    for attempt in range(1, repeat + 1):
        for case in cases:
            case_result = run_eval_case(
                case,
                runs_root=runs_root,
                agent_runner=agent_runner,
                attempt=attempt,
                preserve_run=True,
                suite_id=suite_id,
                milestone=milestone,
                stage=stage,
                configuration=configuration_snapshot,
            )
            results.append(case_result)
            if on_case_completed is not None:
                on_case_completed(case_result)
    return EvalSuiteResult(
        passed=all(result.passed for result in results),
        cases=results,
        repeat=repeat,
        configuration=configuration_snapshot,
        suite_id=suite_id,
        milestone=milestone,
        stage=stage,
        created_at=created_at,
    )


def run_eval_case(
    case: EvalCase,
    *,
    runs_root: Path,
    agent_runner: AgentRunCallable,
    attempt: int = 1,
    preserve_run: bool = False,
    suite_id: str = "",
    milestone: str = "",
    stage: str = "development_precheck",
    configuration: dict | None = None,
) -> EvalCaseResult:
    del preserve_run  # V2.0.2 run evidence is always uniquely named and never overwritten.
    suite_id = suite_id or new_suite_id()
    run_id = f"eval-{case.name}-r{attempt}-{new_run_id()}"
    workspace = prepare_run_workspace(case.fixture_dir, run_id=run_id, runs_root=runs_root)
    state = RunState.start(
        case.prompt,
        workspace_host_path=workspace.workspace_dir,
        run_dir=workspace.run_dir,
        artifacts_dir=workspace.artifacts_dir,
        suite_id=suite_id,
        milestone=milestone,
        stage=stage,
    )
    state.run_id = workspace.run_id
    actual_authority_profile = {
        "source_path": case_source_path(case, project_root=Path.cwd()),
        "fixture_source_path": fixture_source_path(
            case,
            project_root=Path.cwd(),
        ),
        "case_definition_sha256": case.definition_sha256,
        "fixture_content_sha256": workspace.content_digest_sha256,
    }
    expected_profiles = (configuration or {}).get("case_authority_profiles")
    expected_profile = (
        expected_profiles.get(case.name)
        if isinstance(expected_profiles, Mapping)
        else None
    )
    authority_profile_required = (
        "2.1.2" in milestone
        and (configuration or {}).get("release_gate") is True
    )
    try:
        if authority_profile_required and expected_profile is None:
            raise RuntimeError(
                f"formal case authority profile is missing: {case.name}"
            )
        if expected_profile is not None and expected_profile != actual_authority_profile:
            raise RuntimeError(
                f"case authority profile changed before snapshot: {case.name}"
            )
        initial_verification = None
        if case.initial_verify is not None:
            initial_spec = {**case.initial_verify, "_artifact_label": "initial"}
            initial_verification = run_assertion(
                initial_spec,
                workspace_dir=workspace.workspace_dir,
                run_dir=workspace.run_dir,
                metrics=state.metrics,
                verifier_dir=case.case_dir / "verifier",
            )
            if not initial_verification.passed:
                raise RuntimeError(
                    "Initial verification did not fail as declared: "
                    + initial_verification.message
                )
        state = agent_runner(case, state)
    except Exception as exc:
        if "initial_verification" not in locals():
            initial_verification = None
        state.status = "failed"
        state.state_summary = _format_infrastructure_error(exc)
        state.metrics["infrastructure_errors"] = state.metrics.get("infrastructure_errors", 0) + 1
        save_run_state(state)
    write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir)
    metrics = _load_metrics(workspace.run_dir)
    assertion_results = run_assertions(
        case.assertions,
        workspace_dir=workspace.workspace_dir,
        run_dir=workspace.run_dir,
        metrics=metrics or state.metrics,
        verifier_dir=case.case_dir / "verifier",
    )
    if initial_verification is not None:
        assertion_results.insert(0, AssertionResult(
            "initial_verify",
            initial_verification.passed,
            initial_verification.message,
        ))
    expected_status = _expected_run_status(case)
    agent_success = state.status == expected_status
    task_assertions = [result for result in assertion_results if result.type != "run_status"]
    task_success = all(result.passed for result in task_assertions)
    result_metrics = metrics or state.metrics
    infrastructure_success = (
        int(result_metrics.get("provider_errors", 0)) == 0
        and int(result_metrics.get("infrastructure_errors", 0)) == 0
    )
    policy_outcome = "denied" if int(result_metrics.get("policy_denials", 0) or 0) else "clear"
    passed = agent_success and infrastructure_success and all(result.passed for result in assertion_results)
    request_rows, trace_assertion_events = _trace_evidence_rows(
        workspace.run_dir / "trace.jsonl"
    )
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
        task_success=task_success,
        agent_success=agent_success,
        infrastructure_success=infrastructure_success,
        policy_outcome=policy_outcome,
        suite_id=suite_id,
        milestone=milestone,
        stage=stage,
        configuration=dict(configuration or {}),
        case_source_path=actual_authority_profile["source_path"],
        fixture_source_path=actual_authority_profile["fixture_source_path"],
        case_definition_sha256=case.definition_sha256,
        fixture_content_sha256=workspace.content_digest_sha256,
        request_rows=request_rows,
        trace_assertion_events=trace_assertion_events,
        assertion_specs=[dict(assertion) for assertion in case.assertions],
        initial_verification=initial_verification,
        verdict=_eval_verdict(passed, infrastructure_success, result_metrics),
    )
    cleanup_error = ""
    workspace_cleaned = False
    if case.cleanup_workspace:
        try:
            shutil.rmtree(workspace.workspace_dir, onerror=_retry_readonly_removal)
            workspace_cleaned = True
        except OSError as exc:
            cleanup_error = str(exc)
            result.metrics["workspace_cleanup_error"] = cleanup_error
            (workspace.run_dir / "cleanup_error.txt").write_text(cleanup_error + "\n", encoding="utf-8")
    if workspace_cleaned:
        result.metrics["workspace_cleaned"] = True
    result = replace(
        result,
        passed=result.passed and not cleanup_error,
        infrastructure_success=result.infrastructure_success and not cleanup_error,
        workspace_cleaned=workspace_cleaned,
        cleanup_error=cleanup_error,
        verdict=(
            "infrastructure_error"
            if cleanup_error
            else _eval_verdict(result.passed, result.infrastructure_success, result.metrics)
        ),
    )
    write_eval_case_report(result, workspace.run_dir)
    write_artifact_index(
        workspace.run_dir.parent.parent / "artifacts",
        run_id=result.run_id,
        run_dir=workspace.run_dir,
        evidence=_run_evidence_paths(result),
        hash_artifacts=True,
    )
    return result


def _retry_readonly_removal(
    operation: Callable[[str], object],
    path: str,
    error_info: tuple[type[BaseException], BaseException, TracebackType | None],
) -> None:
    error = error_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    operation(path)


def _eval_verdict(passed: bool, infrastructure_success: bool, metrics: Mapping[str, Any]) -> str:
    if passed:
        return "passed"
    if int(metrics.get("timeouts", 0) or 0) > 0:
        return "timeout"
    if not infrastructure_success:
        return "infrastructure_error"
    return "failed"


def write_eval_report(result: EvalSuiteResult, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    csv_path = output_dir / "report.csv"
    manifest_path = output_dir / "manifest.json"
    existing = [path for path in (json_path, markdown_path, csv_path, manifest_path) if path.exists()]
    if existing:
        raise FileExistsError(f"Suite report export is immutable and already exists: {existing[0]}")
    report, manifest = _suite_payloads(result)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(format_markdown_report(result), encoding="utf-8")
    csv_path.write_text(format_csv_report(result), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return json_path, markdown_path


def write_suite_report(result: EvalSuiteResult, suites_root: Path) -> SuiteBundle:
    report, manifest = _suite_payloads(result)
    return write_immutable_suite(
        suites_root,
        suite_id=result.suite_id,
        manifest=manifest,
        report=report,
        markdown=format_markdown_report(result),
        csv_text=format_csv_report(result),
    )


def suite_to_dict(result: EvalSuiteResult) -> dict:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "suite_report",
        "suite_id": result.suite_id,
        "milestone": result.milestone,
        "stage": result.stage,
        "created_at": result.created_at,
        "completed_at": _suite_completed_at(result),
        "status": "completed",
        "result": "PASS" if result.passed else "FAIL",
        "passed": result.passed,
        "repeat": result.repeat,
        "configuration": result.configuration or {},
        "case_summary": aggregate_case_results(result.cases),
        "cases": [
            {
                "name": case.name,
                "schema_version": LEDGER_SCHEMA_VERSION,
                "suite_id": case.suite_id,
                "milestone": case.milestone,
                "stage": case.stage,
                "capability": case.capability,
                "passed": case.passed,
                "verdict": case.verdict,
                "run_status": case.run_status,
                "attempt": case.attempt,
                "run_id": case.run_id,
                "run_dir": case.run_dir,
                "sandbox_mode": case.sandbox_mode,
                "budget": case.budget or {},
                "proves": case.proves,
                "task_success": case.task_success,
                "agent_success": case.agent_success,
                "infrastructure_success": case.infrastructure_success,
                "policy_outcome": case.policy_outcome,
                "formal_metric_eligible": _formal_metric_eligible(case),
                "case_source_path": case.case_source_path,
                "fixture_source_path": case.fixture_source_path,
                "case_definition_sha256": case.case_definition_sha256,
                "fixture_content_sha256": case.fixture_content_sha256,
                "request_rows": case.request_rows,
                "trace_assertion_events": case.trace_assertion_events,
                "assertion_specs": case.assertion_specs,
                "evidence": _run_evidence_paths(case),
                "metrics": case.metrics,
                "assertions": [asdict(assertion) for assertion in case.assertions],
                "initial_verification": (
                    asdict(case.initial_verification) if case.initial_verification is not None else None
                ),
                "workspace_cleaned": case.workspace_cleaned,
                "cleanup_error": case.cleanup_error,
            }
            for case in result.cases
        ],
    }


def format_markdown_report(result: EvalSuiteResult) -> str:
    lines = [
        "# miniCC eval report",
        "",
        f"Overall: {'PASS' if result.passed else 'FAIL'}",
        f"Suite: `{result.suite_id}`",
        f"Milestone: `{result.milestone}`",
        f"Stage: `{result.stage}`",
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
            f"task={summary['task_success_runs']}/{summary['attempts']}, "
            f"agent={summary['agent_success_runs']}/{summary['attempts']}, "
            f"infrastructure={summary['infrastructure_success_runs']}/{summary['attempts']}, "
            f"policy_clear={summary['policy_clear_runs']}/{summary['attempts']}, "
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
        lines.append(f"Verdict: `{case.verdict}`")
        lines.append(
            "Outcome: "
            f"task={'PASS' if case.task_success else 'FAIL'}, "
            f"agent={'PASS' if case.agent_success else 'FAIL'}, "
            f"infrastructure={'PASS' if case.infrastructure_success else 'FAIL'}"
        )
        lines.append(f"Policy outcome: `{case.policy_outcome}`")
        lines.append(
            f"Workspace cleaned: `{'true' if case.workspace_cleaned else 'false'}`"
            + (f" (error: {case.cleanup_error})" if case.cleanup_error else "")
        )
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


def format_csv_report(result: EvalSuiteResult) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "schema_version",
            "suite_id",
            "run_id",
            "milestone",
            "stage",
            "case_name",
            "attempt",
            "status",
            "result",
            "verdict",
            "task_success",
            "agent_success",
            "infrastructure_success",
            "policy_outcome",
            "workspace_cleaned",
            "cleanup_error",
        ]
    )
    for case in result.cases:
        writer.writerow(
            [
                LEDGER_SCHEMA_VERSION,
                case.suite_id,
                case.run_id,
                case.milestone,
                case.stage,
                case.name,
                case.attempt,
                case.run_status,
                "PASS" if case.passed else "FAIL",
                case.verdict,
                case.task_success,
                case.agent_success,
                case.infrastructure_success,
                case.policy_outcome,
                case.workspace_cleaned,
                case.cleanup_error,
            ]
        )
    return buffer.getvalue()


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
                "task_success_runs": sum(getattr(result, "task_success", result.passed) for result in results),
                "agent_success_runs": sum(getattr(result, "agent_success", result.passed) for result in results),
                "infrastructure_success_runs": sum(
                    getattr(result, "infrastructure_success", result.passed) for result in results
                ),
                "policy_clear_runs": sum(
                    getattr(result, "policy_outcome", "unknown") == "clear" for result in results
                ),
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
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "run_result",
        "suite_id": result.suite_id,
        "milestone": result.milestone,
        "stage": result.stage,
        "name": result.name,
        "case_name": result.name,
        "capability": result.capability,
        "attempt": result.attempt,
        "passed": result.passed,
        "verdict": result.verdict,
        "run_id": result.run_id,
        "run_status": result.run_status,
        "task_success": result.task_success,
        "agent_success": result.agent_success,
        "infrastructure_success": result.infrastructure_success,
        "policy_outcome": result.policy_outcome,
        "result": "PASS" if result.passed else "FAIL",
        "source_commit": str((result.configuration or {}).get("git_commit") or ""),
        "case_source_path": result.case_source_path,
        "fixture_source_path": result.fixture_source_path,
        "case_definition_sha256": result.case_definition_sha256,
        "fixture_content_sha256": result.fixture_content_sha256,
        "request_rows": result.request_rows,
        "trace_assertion_events": result.trace_assertion_events,
        "assertion_specs": result.assertion_specs,
        "workspace_manifest": str((run_dir / "workspace_manifest.json").resolve()),
        "provider": {
            "base_url": str((result.configuration or {}).get("base_url") or ""),
            "model": str((result.configuration or {}).get("model") or ""),
            "temperature": (result.configuration or {}).get("temperature"),
        },
        "sandbox": {
            "mode": result.sandbox_mode,
            "image": str((result.configuration or {}).get("docker_image") or ""),
        },
        "started_at": result.metrics.get("started_at"),
        "completed_at": result.metrics.get("completed_at"),
        "evidence": _run_evidence_paths(result),
        "sandbox_mode": result.sandbox_mode,
        "budget": result.budget or {},
        "metrics": result.metrics,
        "assertions": [asdict(assertion) for assertion in result.assertions],
        "initial_verification": (
            asdict(result.initial_verification) if result.initial_verification is not None else None
        ),
        "workspace_cleaned": result.workspace_cleaned,
        "cleanup_error": result.cleanup_error,
    }
    json_path = run_dir / "eval_result.json"
    markdown_path = run_dir / "eval_result.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# {result.name} attempt {result.attempt}",
        "",
        f"- Passed: `{'true' if result.passed else 'false'}`",
        f"- Verdict: `{result.verdict}`",
        f"- Run status: `{payload['run_status']}`",
        f"- Task success: `{'true' if result.task_success else 'false'}`",
        f"- Agent success: `{'true' if result.agent_success else 'false'}`",
        f"- Infrastructure success: `{'true' if result.infrastructure_success else 'false'}`",
        f"- Policy outcome: `{result.policy_outcome}`",
        f"- Suite id: `{result.suite_id}`",
        f"- Run id: `{result.run_id}`",
        f"- Sandbox mode: `{result.sandbox_mode}`",
        f"- Workspace cleaned: `{'true' if result.workspace_cleaned else 'false'}`",
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
    bundle = write_suite_report(result, runs_root.parent / "suites")
    return bundle.report_json_path, bundle.report_markdown_path


def _suite_payloads(result: EvalSuiteResult) -> tuple[dict, dict]:
    report = suite_to_dict(result)
    run_entries = [
        {
            "run_id": case.run_id,
            "run_dir": str(Path(case.run_dir).resolve()),
            "case_name": case.name,
            "attempt": case.attempt,
            "status": case.run_status,
            "result": "PASS" if case.passed else "FAIL",
            "verdict": case.verdict,
            "task_success": case.task_success,
            "agent_success": case.agent_success,
            "infrastructure_success": case.infrastructure_success,
            "policy_outcome": case.policy_outcome,
            "workspace_cleaned": case.workspace_cleaned,
            "cleanup_error": case.cleanup_error,
            "evidence": _run_evidence_paths(case),
        }
        for case in result.cases
    ]
    manifest = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "suite",
        "suite_id": result.suite_id,
        "milestone": result.milestone,
        "stage": result.stage,
        "created_at": result.created_at,
        "completed_at": _suite_completed_at(result),
        "status": "completed",
        "result": "PASS" if result.passed else "FAIL",
        "task_success": all(case.task_success for case in result.cases),
        "agent_success": all(case.agent_success for case in result.cases),
        "infrastructure_success": all(case.infrastructure_success for case in result.cases),
        "policy_outcome": (
            "denied" if any(case.policy_outcome == "denied" for case in result.cases) else "clear"
        ),
        "configuration": dict(result.configuration or {}),
        "run_ids": [case.run_id for case in result.cases],
        "runs": run_entries,
        "reports": {
            "json": "report.json",
            "markdown": "report.md",
            "csv": "report.csv",
        },
    }
    return report, manifest


def _run_evidence_paths(result: EvalCaseResult) -> dict[str, str]:
    run_dir = Path(result.run_dir).resolve()
    suite_manifest = run_dir.parent.parent / "suites" / result.suite_id / "manifest.json"
    evidence = {
        "state": str(run_dir / "state.json"),
        "trace": str(run_dir / "trace.jsonl"),
        "metrics": str(run_dir / "metrics.json"),
        "workspace_manifest": str(run_dir / "workspace_manifest.json"),
        "diff": str(run_dir / "artifacts" / "diff.patch"),
        "run_report": str(run_dir / "eval_result.json"),
        "suite_manifest": str(suite_manifest),
    }
    working_memory = run_dir / "working_memory.json"
    if working_memory.is_file():
        evidence["working_memory"] = str(working_memory)
    verification_dir = run_dir / "artifacts" / "verification"
    for verification in sorted(verification_dir.glob("*.json")):
        evidence[f"verification_{verification.stem}"] = str(verification)
    return evidence


def _trace_evidence_rows(
    trace_path: Path,
) -> tuple[list[dict], list[dict]]:
    if not trace_path.is_file():
        return [], []
    request_rows: list[dict] = []
    trace_events: list[dict] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        trace_events.append(event)
        event_type = event.get("event")
        if event_type == "model_response":
            request_rows.append(event)
    return request_rows, trace_action_shape_evidence_events(trace_events)


def _formal_metric_eligible(result: EvalCaseResult) -> bool:
    return (
        result.stage == "formal_acceptance"
        and result.run_status in {"completed", "failed"}
        and all(Path(path).is_file() for key, path in _run_evidence_paths(result).items() if key != "suite_manifest")
    )


def _suite_completed_at(result: EvalSuiteResult) -> str | None:
    timestamps = [str(case.metrics.get("completed_at")) for case in result.cases if case.metrics.get("completed_at")]
    return max(timestamps) if timestamps else None


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


def _format_infrastructure_error(exc: Exception) -> str:
    message = f"Evaluation infrastructure failed: {type(exc).__name__}: {exc}"
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if stderr:
            message += f"\nstderr={str(stderr).strip()[-4000:]}"
    return message
