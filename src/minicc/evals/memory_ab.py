from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from minicc.core.ledger import LEDGER_SCHEMA_VERSION, SuiteBundle, new_suite_id, write_immutable_suite
from minicc.core.state import RunState
from minicc.evals.case import EvalCase, load_case
from minicc.evals.runner import EvalCaseResult, run_eval_case


MemoryVariant = Literal["m0", "m1"]
MemoryAgentRunner = Callable[[EvalCase, RunState, str | None], RunState]


@dataclass(frozen=True)
class FollowUpCase:
    source: EvalCase
    follow_up: EvalCase
    expected_memory_paths: tuple[str, ...]


@dataclass(frozen=True)
class MemoryABResult:
    suite_id: str
    milestone: str
    stage: str
    created_at: str
    completed_at: str
    repeat: int
    passed: bool
    configuration: dict[str, Any]
    attempts: list[dict[str, Any]]
    case_results: list[EvalCaseResult]


def load_follow_up_case(path: Path) -> FollowUpCase:
    case_path = path / "case.yaml" if path.is_dir() else path
    source_case = load_case(case_path)
    payload = yaml.safe_load(case_path.read_text(encoding="utf-8")) or {}
    follow_up = payload.get("follow_up") if isinstance(payload, dict) else None
    if not isinstance(follow_up, dict):
        raise ValueError(f"Memory eval case requires a follow_up mapping: {case_path}")
    prompt = follow_up.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"Memory eval case requires follow_up.prompt: {case_path}")
    assertions = follow_up.get("assertions", [])
    if not isinstance(assertions, list) or not all(isinstance(item, dict) for item in assertions):
        raise ValueError(f"follow_up.assertions must be a list of mappings: {case_path}")
    paths = follow_up.get("expected_memory_paths", [])
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(item, str) and item.strip() for item in paths)
    ):
        raise ValueError(f"follow_up.expected_memory_paths must be non-empty: {case_path}")
    follow_case = replace(
        source_case,
        name=f"{source_case.name}_follow_up",
        prompt=prompt.strip(),
        capability="working_memory_follow_up",
        proves=str(follow_up.get("proves") or ""),
        assertions=[dict(item) for item in assertions],
    )
    return FollowUpCase(
        source=replace(source_case, name=f"{source_case.name}_source"),
        follow_up=follow_case,
        expected_memory_paths=tuple(dict.fromkeys(item.replace("\\", "/") for item in paths)),
    )


def run_memory_ab(
    case: FollowUpCase,
    *,
    runs_root: Path,
    agent_runner: MemoryAgentRunner,
    repeat: int = 3,
    execution_order: Literal["alternating", "m0-first", "m1-first"] = "alternating",
    configuration: dict[str, Any] | None = None,
    milestone: str = "stable-v2.2",
    stage: str = "development_precheck",
    suite_id: str | None = None,
) -> MemoryABResult:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    suite_id = suite_id or new_suite_id()
    created_at = datetime.now(timezone.utc).isoformat()
    attempts: list[dict[str, Any]] = []
    all_results: list[EvalCaseResult] = []
    for attempt in range(1, repeat + 1):
        source_result = run_eval_case(
            case.source,
            runs_root=runs_root,
            agent_runner=lambda eval_case, state: agent_runner(eval_case, state, None),
            attempt=attempt,
            suite_id=suite_id,
            milestone=milestone,
            stage=stage,
            configuration={**dict(configuration or {}), "memory_variant": "source"},
        )
        all_results.append(source_result)
        variant_rows: dict[str, dict[str, Any]] = {}
        order = _variant_order(execution_order, attempt)
        if source_result.passed:
            for variant in order:
                source_run_id = source_result.run_id if variant == "m1" else None
                variant_case = replace(case.follow_up, name=f"{case.follow_up.name}_{variant}")
                result = run_eval_case(
                    variant_case,
                    runs_root=runs_root,
                    agent_runner=lambda eval_case, state, source_run_id=source_run_id: agent_runner(
                        eval_case,
                        state,
                        source_run_id,
                    ),
                    attempt=attempt,
                    suite_id=suite_id,
                    milestone=milestone,
                    stage=stage,
                    configuration={**dict(configuration or {}), "memory_variant": variant},
                )
                all_results.append(result)
                variant_rows[variant] = _variant_evidence(
                    result,
                    variant=variant,
                    paired_source_run_id=source_result.run_id,
                    expected_paths=case.expected_memory_paths,
                )
        source_evidence = _source_evidence(source_result, case.expected_memory_paths)
        m0 = variant_rows.get("m0", _missing_variant("m0"))
        m1 = variant_rows.get("m1", _missing_variant("m1"))
        attempts.append(
            {
                "attempt": attempt,
                "execution_order": list(order),
                "source": source_evidence,
                "m0": m0,
                "m1": m1,
                "paired_read_reduction": (
                    m0["repeated_source_file_reads"] - m1["repeated_source_file_reads"]
                ),
                "paired_read_decreased": (
                    m1["repeated_source_file_reads"] < m0["repeated_source_file_reads"]
                ),
            }
        )
    completed_at = datetime.now(timezone.utc).isoformat()
    passed = _overall_passed(attempts, repeat)
    return MemoryABResult(
        suite_id=suite_id,
        milestone=milestone,
        stage=stage,
        created_at=created_at,
        completed_at=completed_at,
        repeat=repeat,
        passed=passed,
        configuration=dict(configuration or {}),
        attempts=attempts,
        case_results=all_results,
    )


def write_memory_ab_report(result: MemoryABResult, suites_root: Path) -> SuiteBundle:
    report = memory_ab_to_dict(result)
    manifest = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "suite",
        "suite_type": "working_memory_ab",
        "suite_id": result.suite_id,
        "milestone": result.milestone,
        "stage": result.stage,
        "created_at": result.created_at,
        "completed_at": result.completed_at,
        "status": "completed",
        "result": "PASS" if result.passed else "FAIL",
        "task_success": all(item.task_success for item in result.case_results),
        "agent_success": all(item.agent_success for item in result.case_results),
        "infrastructure_success": all(item.infrastructure_success for item in result.case_results),
        "policy_outcome": (
            "denied" if any(item.policy_outcome == "denied" for item in result.case_results) else "clear"
        ),
        "configuration": result.configuration,
        "run_ids": [item.run_id for item in result.case_results],
        "runs": [_manifest_run(item) for item in result.case_results],
        "reports": {"json": "report.json", "markdown": "report.md", "csv": "report.csv"},
    }
    return write_immutable_suite(
        suites_root,
        suite_id=result.suite_id,
        manifest=manifest,
        report=report,
        markdown=format_memory_ab_markdown(report),
        csv_text=format_memory_ab_csv(report),
    )


def memory_ab_to_dict(result: MemoryABResult) -> dict[str, Any]:
    m0_rows = [attempt["m0"] for attempt in result.attempts]
    m1_rows = [attempt["m1"] for attempt in result.attempts]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "working_memory_ab_report",
        "suite_id": result.suite_id,
        "milestone": result.milestone,
        "stage": result.stage,
        "created_at": result.created_at,
        "completed_at": result.completed_at,
        "result": "PASS" if result.passed else "FAIL",
        "passed": result.passed,
        "repeat": result.repeat,
        "configuration": result.configuration,
        "criteria": {
            "source_runs_passed": all(attempt["source"]["passed"] for attempt in result.attempts),
            "follow_up_key_fact_accuracy_m0": _accuracy(m0_rows),
            "follow_up_key_fact_accuracy_m1": _accuracy(m1_rows),
            "memory_reduces_reads_every_pair": all(
                attempt["paired_read_decreased"] for attempt in result.attempts
            ),
            "old_run_memory_leaks": sum(row["old_run_memory_leaks"] for row in m1_rows),
            "irrelevant_memory_injections": sum(
                row["irrelevant_memory_injections"] for row in m1_rows
            ),
            "integrity_invalid_memory_adoptions": sum(
                row["integrity_invalid_memory_adoptions"] for row in m1_rows
            ),
        },
        "aggregate": {
            "m0_repeated_source_file_reads": sum(row["repeated_source_file_reads"] for row in m0_rows),
            "m1_repeated_source_file_reads": sum(row["repeated_source_file_reads"] for row in m1_rows),
            "m0_prompt_tokens": sum(row["prompt_tokens"] for row in m0_rows),
            "m1_prompt_tokens": sum(row["prompt_tokens"] for row in m1_rows),
        },
        "attempts": result.attempts,
    }


def format_memory_ab_markdown(report: dict[str, Any]) -> str:
    criteria = report["criteria"]
    aggregate = report["aggregate"]
    lines = [
        "# miniCC V2.2 working-memory A/B report",
        "",
        f"Overall: {report['result']}",
        f"Suite: `{report['suite_id']}`",
        f"Repeat: {report['repeat']}",
        "",
        "## Criteria",
        "",
        f"- Source runs passed: `{criteria['source_runs_passed']}`",
        f"- Follow-up key-fact accuracy M0/M1: `{criteria['follow_up_key_fact_accuracy_m0']:.2%}` / `{criteria['follow_up_key_fact_accuracy_m1']:.2%}`",
        f"- Read reduction in every pair: `{criteria['memory_reduces_reads_every_pair']}`",
        f"- Old-run leaks: `{criteria['old_run_memory_leaks']}`",
        f"- Irrelevant injections: `{criteria['irrelevant_memory_injections']}`",
        f"- Integrity-invalid adoptions: `{criteria['integrity_invalid_memory_adoptions']}`",
        "",
        "## Aggregate",
        "",
        f"- Repeated source-file reads M0 -> M1: `{aggregate['m0_repeated_source_file_reads']} -> {aggregate['m1_repeated_source_file_reads']}`",
        f"- Follow-up prompt tokens M0 -> M1: `{aggregate['m0_prompt_tokens']} -> {aggregate['m1_prompt_tokens']}`",
        "",
        "## Attempts and raw commands",
        "",
    ]
    for attempt in report["attempts"]:
        lines.extend(
            [
                f"### Attempt {attempt['attempt']}",
                "",
                f"- Source run: `{attempt['source']['run_id']}`",
                f"- M0 run/reads: `{attempt['m0']['run_id']}` / `{attempt['m0']['repeated_source_file_reads']}`",
                f"- M1 run/reads: `{attempt['m1']['run_id']}` / `{attempt['m1']['repeated_source_file_reads']}`",
                f"- M0 commands: `{json.dumps(attempt['m0']['commands'], ensure_ascii=False)}`",
                f"- M1 commands: `{json.dumps(attempt['m1']['commands'], ensure_ascii=False)}`",
                "",
            ]
        )
    return "\n".join(lines)


def format_memory_ab_csv(report: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "attempt",
            "variant",
            "run_id",
            "passed",
            "repeated_source_file_reads",
            "prompt_tokens",
            "old_run_memory_leaks",
            "irrelevant_memory_injections",
            "integrity_invalid_memory_adoptions",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for attempt in report["attempts"]:
        for variant in ("m0", "m1"):
            row = attempt[variant]
            writer.writerow({"attempt": attempt["attempt"], "variant": variant, **{key: row[key] for key in writer.fieldnames if key not in {"attempt", "variant"}}})
    return output.getvalue()


def _source_evidence(result: EvalCaseResult, expected_paths: tuple[str, ...]) -> dict[str, Any]:
    events = _read_trace(Path(result.run_dir) / "trace.jsonl")
    captured = [
        event.get("reference")
        for event in events
        if event.get("event") == "memory_reference_captured" and isinstance(event.get("reference"), dict)
    ]
    memory_path = Path(result.run_dir) / "working_memory.json"
    return {
        "run_id": result.run_id,
        "run_dir": result.run_dir,
        "passed": result.passed and {item.get("path") for item in captured} == set(expected_paths),
        "prompt_tokens": int(result.metrics.get("prompt_tokens", 0) or 0),
        "captured_references": captured,
        "working_memory_path": str(memory_path),
        "working_memory_sha256": (
            hashlib.sha256(memory_path.read_bytes()).hexdigest() if memory_path.is_file() else ""
        ),
        "commands": _bash_commands(events),
    }


def _variant_evidence(
    result: EvalCaseResult,
    *,
    variant: MemoryVariant,
    paired_source_run_id: str,
    expected_paths: tuple[str, ...],
) -> dict[str, Any]:
    events = _read_trace(Path(result.run_dir) / "trace.jsonl")
    commands = _bash_commands(events)
    injection_events = [event for event in events if event.get("event") == "working_memory_injected"]
    injected_paths = [
        str(reference.get("path") or "")
        for event in injection_events
        for reference in event.get("references", [])
        if isinstance(reference, dict)
    ]
    source_ids = [str(event.get("source_run_id") or "") for event in injection_events]
    expected = set(expected_paths)
    return {
        "variant": variant,
        "run_id": result.run_id,
        "run_dir": result.run_dir,
        "passed": result.passed,
        "key_facts_correct": result.passed,
        "prompt_tokens": int(result.metrics.get("prompt_tokens", 0) or 0),
        "repeated_source_file_reads": _count_expected_file_reads(commands, expected_paths),
        "commands": commands,
        "memory_injection_events": len(injection_events),
        "memory_items_injected": len(injected_paths),
        "old_run_memory_leaks": sum(source_id != paired_source_run_id for source_id in source_ids),
        "irrelevant_memory_injections": sum(path not in expected for path in injected_paths),
        "integrity_invalid_memory_adoptions": int(
            result.metrics.get("working_memory_invalid_adoptions", 0) or 0
        ),
    }


def _overall_passed(attempts: list[dict[str, Any]], repeat: int) -> bool:
    if len(attempts) != repeat:
        return False
    return all(
        attempt["source"]["passed"]
        and attempt["m0"]["passed"]
        and attempt["m1"]["passed"]
        and attempt["m0"]["memory_injection_events"] == 0
        and attempt["m1"]["memory_injection_events"] == 1
        and attempt["m1"]["memory_items_injected"] > 0
        and attempt["paired_read_decreased"]
        and attempt["m1"]["old_run_memory_leaks"] == 0
        and attempt["m1"]["irrelevant_memory_injections"] == 0
        and attempt["m1"]["integrity_invalid_memory_adoptions"] == 0
        for attempt in attempts
    )


def _variant_order(order: str, attempt: int) -> tuple[MemoryVariant, MemoryVariant]:
    if order == "m1-first" or (order == "alternating" and attempt % 2 == 0):
        return "m1", "m0"
    return "m0", "m1"


def _count_expected_file_reads(commands: list[str], paths: tuple[str, ...]) -> int:
    return sum(
        1
        for command in commands
        if _is_read_command(command) and any(path in command.replace("\\", "/") for path in paths)
    )


def _is_read_command(command: str) -> bool:
    lowered = command.lower()
    return bool(
        re.search(r"(^|[;&|\r\n]\s*)(cat|head|tail|sed|rg|grep|find|fd)(\.exe)?\b", lowered)
        or "get-content" in lowered
        or "select-string" in lowered
    )


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _bash_commands(events: list[dict[str, Any]]) -> list[str]:
    return [
        str(action.get("command") or "")
        for event in events
        if event.get("event") == "action_parsed"
        and isinstance((action := event.get("action")), dict)
        and action.get("type") == "bash"
    ]


def _accuracy(rows: list[dict[str, Any]]) -> float:
    return sum(bool(row["key_facts_correct"]) for row in rows) / len(rows) if rows else 0.0


def _missing_variant(variant: MemoryVariant) -> dict[str, Any]:
    return {
        "variant": variant,
        "run_id": "",
        "run_dir": "",
        "passed": False,
        "key_facts_correct": False,
        "prompt_tokens": 0,
        "repeated_source_file_reads": 0,
        "commands": [],
        "memory_injection_events": 0,
        "memory_items_injected": 0,
        "old_run_memory_leaks": 0,
        "irrelevant_memory_injections": 0,
        "integrity_invalid_memory_adoptions": 0,
    }


def _manifest_run(result: EvalCaseResult) -> dict[str, Any]:
    run_dir = Path(result.run_dir).resolve()
    evidence = {
        "state": str(run_dir / "state.json"),
        "trace": str(run_dir / "trace.jsonl"),
        "metrics": str(run_dir / "metrics.json"),
        "workspace_manifest": str(run_dir / "workspace_manifest.json"),
        "diff": str(run_dir / "artifacts" / "diff.patch"),
        "run_report": str(run_dir / "run_report.json"),
        "eval_result": str(run_dir / "eval_result.json"),
    }
    if (run_dir / "working_memory.json").is_file():
        evidence["working_memory"] = str(run_dir / "working_memory.json")
    return {
        "run_id": result.run_id,
        "run_dir": str(run_dir),
        "case_name": result.name,
        "attempt": result.attempt,
        "status": result.run_status,
        "result": "PASS" if result.passed else "FAIL",
        "task_success": result.task_success,
        "agent_success": result.agent_success,
        "infrastructure_success": result.infrastructure_success,
        "policy_outcome": result.policy_outcome,
        "evidence": evidence,
    }
