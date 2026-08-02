from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from minicc.core.ledger import inspect_run


REQUIRED_MEMORY_CASES = {
    "M01_service_contract_follow_up": (
        "eval_cases/memory_suite_v1/M01_service_contract_follow_up/case.yaml",
        "eval_cases/memory_suite_v1/M01_service_contract_follow_up/fixture",
    ),
    "M02_deploy_cli_follow_up": (
        "eval_cases/memory_suite_v1/M02_deploy_cli_follow_up/case.yaml",
        "eval_cases/memory_suite_v1/M02_deploy_cli_follow_up/fixture",
    ),
    "M03_validator_contract_follow_up": (
        "eval_cases/memory_suite_v1/M03_validator_contract_follow_up/case.yaml",
        "eval_cases/memory_suite_v1/M03_validator_contract_follow_up/fixture",
    ),
}


@dataclass(frozen=True)
class MemoryAcceptanceBundle:
    json_path: Path
    markdown_path: Path
    evidence_path: Path
    manifest_path: Path


def load_memory_suite_report(path: Path, *, verify_manifest: bool = True) -> dict[str, Any]:
    report_path = path.resolve()
    manifest_path = report_path.parent / "manifest.json"
    try:
        report_bytes = report_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
        report = json.loads(report_bytes)
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid working-memory suite evidence: {report_path}") from exc
    if not isinstance(report, dict) or not isinstance(manifest, dict):
        raise ValueError(f"working-memory suite evidence must contain objects: {report_path}")
    if (
        report.get("entity_type") != "working_memory_ab_report"
        or manifest.get("entity_type") != "suite"
        or manifest.get("suite_type") != "working_memory_ab"
        or report.get("suite_id") != manifest.get("suite_id")
        or report.get("milestone") != manifest.get("milestone")
        or report.get("stage") != manifest.get("stage")
        or report.get("result") != manifest.get("result")
    ):
        raise ValueError(f"working-memory suite report and manifest disagree: {report_path}")
    if verify_manifest:
        _verify_suite_artifacts(report_path.parent, manifest, report_bytes)
        runtime = _verify_run_evidence(manifest, report)
    else:
        runtime = {}
    payload = dict(report)
    payload["_evidence_integrity_verified"] = bool(verify_manifest)
    payload["_evidence_source_path"] = str(report_path)
    payload["_evidence_report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
    payload["_evidence_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    payload["_verified_runtime"] = runtime
    return payload


def build_memory_acceptance_report(
    suites: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(suite) for suite in suites]
    case_names = [str((row.get("configuration") or {}).get("case_name") or "") for row in rows]
    configurations = [row.get("configuration") or {} for row in rows]
    locked_keys = (
        "base_url",
        "model",
        "temperature",
        "provider_timeout_sec",
        "provider_max_retries",
        "sandbox_mode",
        "docker_image",
        "git_commit",
        "feedback_memory_mode",
        "working_memory_mode",
        "prompt_layout",
        "compaction_strategy",
        "release_gate",
    )
    locked = {
        key: configurations[0].get(key) if configurations else None
        for key in locked_keys
    }
    exact_cases = len(rows) == 3 and set(case_names) == set(REQUIRED_MEMORY_CASES)
    profiles_valid = exact_cases and all(
        _canonical_profile(case_name, configuration)
        for case_name, configuration in zip(case_names, configurations, strict=True)
    )
    same_configuration = bool(configurations) and all(
        all(configuration.get(key) == locked[key] for key in locked_keys)
        for configuration in configurations
    )
    formal_sources = all(
        row.get("stage") == "formal_acceptance"
        and row.get("passed") is True
        and row.get("result") == "PASS"
        and int(row.get("repeat") or 0) == 3
        and row.get("_evidence_integrity_verified") is True
        and (row.get("configuration") or {}).get("git_preflight_verified") is True
        and (row.get("configuration") or {}).get("git_postflight_verified") is True
        and (row.get("configuration") or {}).get("worktree_dirty") is False
        for row in rows
    )
    attempts = [
        attempt
        for row in rows
        for attempt in row.get("attempts", [])
        if isinstance(attempt, Mapping)
    ]
    m0_rows = [attempt.get("m0") or {} for attempt in attempts]
    m1_rows = [attempt.get("m1") or {} for attempt in attempts]
    runtime = [_runtime(row) for row in rows]
    configured_model = str(locked.get("model") or "")
    observed_models = sorted(
        {
            model
            for item in runtime
            for model in item.get("provider_response_models", [])
            if isinstance(model, str) and model
        }
    )
    criteria = {
        "exactly_three_canonical_cases": exact_cases,
        "independent_suite_and_run_ids": _independent_ids(rows),
        "locked_configuration_consistent": same_configuration,
        "case_authority_profiles_locked": profiles_valid,
        "all_sources_formal_and_passed": formal_sources,
        "follow_up_key_fact_accuracy_m0": _accuracy(m0_rows),
        "follow_up_key_fact_accuracy_m1": _accuracy(m1_rows),
        "memory_reduces_reads_every_pair": len(attempts) == 9
        and all(attempt.get("paired_read_decreased") is True for attempt in attempts),
        "old_run_memory_leaks": sum(_integer(row.get("old_run_memory_leaks")) for row in m1_rows),
        "irrelevant_memory_injections": sum(
            _integer(row.get("irrelevant_memory_injections")) for row in m1_rows
        ),
        "integrity_invalid_memory_adoptions": sum(
            _integer(row.get("integrity_invalid_memory_adoptions")) for row in m1_rows
        ),
        "provider_errors": sum(_integer(item.get("provider_errors")) for item in runtime),
        "protocol_errors": sum(_integer(item.get("protocol_errors")) for item in runtime),
        "waiting_approval_runs": sum(_integer(item.get("waiting_approval_runs")) for item in runtime),
        "runtime_model_identity_verified": bool(configured_model)
        and observed_models == [configured_model],
    }
    passed = (
        criteria["exactly_three_canonical_cases"]
        and criteria["independent_suite_and_run_ids"]
        and criteria["locked_configuration_consistent"]
        and criteria["case_authority_profiles_locked"]
        and criteria["all_sources_formal_and_passed"]
        and criteria["follow_up_key_fact_accuracy_m0"] == 1.0
        and criteria["follow_up_key_fact_accuracy_m1"] == 1.0
        and criteria["memory_reduces_reads_every_pair"]
        and criteria["old_run_memory_leaks"] == 0
        and criteria["irrelevant_memory_injections"] == 0
        and criteria["integrity_invalid_memory_adoptions"] == 0
        and criteria["provider_errors"] == 0
        and criteria["protocol_errors"] == 0
        and criteria["waiting_approval_runs"] == 0
        and criteria["runtime_model_identity_verified"]
    )
    m0_prompt = sum(_integer(row.get("prompt_tokens")) for row in m0_rows)
    m1_prompt = sum(_integer(row.get("prompt_tokens")) for row in m1_rows)
    m0_reads = sum(_integer(row.get("repeated_source_file_reads")) for row in m0_rows)
    m1_reads = sum(_integer(row.get("repeated_source_file_reads")) for row in m1_rows)
    source_refs = [_source_reference(row) for row in rows]
    return {
        "schema_version": 1,
        "entity_type": "working_memory_acceptance_report",
        "milestone": "stable-v2.2",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "locked_configuration": locked,
        "criteria": criteria,
        "aggregate": {
            "case_count": len(rows),
            "pair_count": len(attempts),
            "run_count": sum(_integer(item.get("run_count")) for item in runtime),
            "m0_repeated_source_file_reads": m0_reads,
            "m1_repeated_source_file_reads": m1_reads,
            "read_reduction": m0_reads - m1_reads,
            "m0_prompt_tokens": m0_prompt,
            "m1_prompt_tokens": m1_prompt,
            "prompt_token_reduction_rate": (
                (m0_prompt - m1_prompt) / m0_prompt if m0_prompt else None
            ),
            "provider_retried_requests": sum(
                _integer(item.get("provider_retried_requests")) for item in runtime
            ),
        },
        "observed_provider_models": observed_models,
        "sources": source_refs,
        "cases": [
            {
                "case_name": case_name,
                "suite_id": row.get("suite_id"),
                "source": source,
                "criteria": row.get("criteria"),
                "aggregate": row.get("aggregate"),
                "runtime": runtime_row,
                "attempts": row.get("attempts"),
            }
            for case_name, row, source, runtime_row in zip(
                case_names,
                rows,
                source_refs,
                runtime,
                strict=True,
            )
        ],
    }


def write_memory_acceptance_report(
    report: Mapping[str, Any],
    output_dir: Path,
) -> MemoryAcceptanceBundle:
    if not bool(report.get("passed")):
        raise ValueError("failed working-memory evidence cannot be written as acceptance")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"working-memory acceptance already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        json_path = temporary / "report.json"
        markdown_path = temporary / "report.md"
        evidence_path = temporary / "evidence.json"
        manifest_path = temporary / "manifest.json"
        json_path.write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(format_memory_acceptance_markdown(report), encoding="utf-8")
        embedded = [
            _embedded_source(source, index=index)
            for index, source in enumerate(report.get("sources", []))
        ]
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "entity_type": "working_memory_source_evidence",
                    "milestone": "stable-v2.2",
                    "source_commit": (report.get("locked_configuration") or {}).get("git_commit"),
                    "inputs": embedded,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "entity_type": "working_memory_acceptance",
                    "milestone": "stable-v2.2",
                    "status": report.get("status"),
                    "source_commit": (report.get("locked_configuration") or {}).get("git_commit"),
                    "input_evidence": [
                        {**dict(source), "embedded_path": f"evidence.json#/inputs/{index}"}
                        for index, source in enumerate(report.get("sources", []))
                    ],
                    "artifacts": {
                        "report_json": _artifact_record(json_path),
                        "report_markdown": _artifact_record(markdown_path),
                        "evidence_bundle": _artifact_record(evidence_path),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return MemoryAcceptanceBundle(
        json_path=output_dir / "report.json",
        markdown_path=output_dir / "report.md",
        evidence_path=output_dir / "evidence.json",
        manifest_path=output_dir / "manifest.json",
    )


def format_memory_acceptance_markdown(report: Mapping[str, Any]) -> str:
    aggregate = report.get("aggregate") or {}
    criteria = report.get("criteria") or {}
    lines = [
        "# miniCC Stable V2.2 Working-Memory Acceptance",
        "",
        f"Status: **{report.get('status')}**",
        f"Source commit: `{(report.get('locked_configuration') or {}).get('git_commit')}`",
        "",
        "## Aggregate",
        "",
        f"- Cases / pairs / runs: `{aggregate.get('case_count')}` / `{aggregate.get('pair_count')}` / `{aggregate.get('run_count')}`",
        f"- Follow-up key-fact accuracy M0/M1: `{criteria.get('follow_up_key_fact_accuracy_m0', 0):.2%}` / `{criteria.get('follow_up_key_fact_accuracy_m1', 0):.2%}`",
        f"- Repeated source-file reads M0 -> M1: `{aggregate.get('m0_repeated_source_file_reads')} -> {aggregate.get('m1_repeated_source_file_reads')}`",
        f"- Prompt tokens M0 -> M1: `{aggregate.get('m0_prompt_tokens')} -> {aggregate.get('m1_prompt_tokens')}`",
        f"- Old-run leaks / irrelevant injections / invalid adoptions: `{criteria.get('old_run_memory_leaks')}` / `{criteria.get('irrelevant_memory_injections')}` / `{criteria.get('integrity_invalid_memory_adoptions')}`",
        f"- Provider retried requests: `{aggregate.get('provider_retried_requests')}`",
        "",
        "## Cases and raw commands",
        "",
    ]
    for case in report.get("cases", []):
        lines.extend([f"### {case.get('case_name')}", "", f"Suite: `{case.get('suite_id')}`", ""])
        for attempt in case.get("attempts", []):
            lines.extend(
                [
                    f"- Attempt {attempt.get('attempt')}: M0 reads `{(attempt.get('m0') or {}).get('repeated_source_file_reads')}`, M1 reads `{(attempt.get('m1') or {}).get('repeated_source_file_reads')}`",
                    f"  - M0 `{json.dumps((attempt.get('m0') or {}).get('commands', []), ensure_ascii=False)}`",
                    f"  - M1 `{json.dumps((attempt.get('m1') or {}).get('commands', []), ensure_ascii=False)}`",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def _verify_suite_artifacts(suite_dir: Path, manifest: Mapping[str, Any], report_bytes: bytes) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"working-memory suite manifest has no artifact hashes: {suite_dir}")
    required = {"report_json", "report_markdown", "report_csv"}
    if set(artifacts) != required:
        raise ValueError(f"working-memory suite manifest artifact set is incomplete: {suite_dir}")
    for name, record in artifacts.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"invalid suite artifact record: {name}")
        path = (suite_dir / str(record.get("path") or "")).resolve()
        if path.parent != suite_dir.resolve() or not path.is_file():
            raise ValueError(f"suite artifact is missing or outside suite: {name}")
        data = report_bytes if name == "report_json" else path.read_bytes()
        if len(data) != _integer(record.get("bytes")) or hashlib.sha256(data).hexdigest() != record.get("sha256"):
            raise ValueError(f"suite artifact integrity check failed: {name}")


def _verify_run_evidence(
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    runs = manifest.get("runs")
    if not isinstance(runs, list) or len(runs) != 9:
        raise ValueError("working-memory formal suite requires exactly 9 runs")
    report_rows: dict[str, tuple[str, Mapping[str, Any], str]] = {}
    for attempt in report.get("attempts", []):
        if not isinstance(attempt, Mapping):
            continue
        source = attempt.get("source") or {}
        source_id = str(source.get("run_id") or "") if isinstance(source, Mapping) else ""
        for role in ("source", "m0", "m1"):
            row = attempt.get(role) or {}
            if isinstance(row, Mapping):
                report_rows[str(row.get("run_id") or "")] = (role, row, source_id)
    report_run_ids = set(report_rows)
    if report_run_ids != {str(run.get("run_id") or "") for run in runs if isinstance(run, Mapping)}:
        raise ValueError("working-memory suite run ids do not match report attempts")
    totals = {
        "run_count": 0,
        "provider_errors": 0,
        "provider_retried_requests": 0,
        "protocol_errors": 0,
        "waiting_approval_runs": 0,
        "provider_response_models": [],
    }
    models: set[str] = set()
    for run in runs:
        if not isinstance(run, Mapping):
            raise ValueError("working-memory suite contains invalid run entry")
        run_id = str(run.get("run_id") or "")
        run_dir = Path(str(run.get("run_dir") or "")).resolve()
        index_path = run_dir.parent.parent / "artifacts" / run_id / "manifest.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"run artifact index is unavailable: {run_id}") from exc
        artifacts = index.get("artifacts") if isinstance(index, Mapping) else None
        if index.get("run_id") != run_id or not isinstance(artifacts, Mapping):
            raise ValueError(f"run artifact index identity is invalid: {run_id}")
        required = {"state", "trace", "metrics", "workspace_manifest", "diff", "run_report"}
        if not required.issubset(artifacts):
            raise ValueError(f"run artifact index is incomplete: {run_id}")
        snapshots: dict[str, bytes] = {}
        for name, record in artifacts.items():
            if not isinstance(record, Mapping):
                raise ValueError(f"invalid run artifact record: {run_id}/{name}")
            path = Path(str(record.get("path") or "")).resolve()
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ValueError(f"run artifact is unavailable: {run_id}/{name}") from exc
            if len(data) != _integer(record.get("bytes")) or hashlib.sha256(data).hexdigest() != record.get("sha256"):
                raise ValueError(f"run artifact integrity check failed: {run_id}/{name}")
            snapshots[name] = data
        state = json.loads(snapshots["state"])
        metrics = json.loads(snapshots["metrics"])
        eval_result = json.loads(snapshots["run_report"])
        inspection = inspect_run(run_dir)
        role, report_row, paired_source_id = report_rows[run_id]
        if (
            state.get("run_id") != run_id
            or metrics.get("run_id") != run_id
            or eval_result.get("run_id") != run_id
            or eval_result.get("suite_id") != report.get("suite_id")
            or state.get("status") != "completed"
            or metrics.get("status") != "completed"
            or eval_result.get("passed") is not True
            or inspection.get("formal_metric_eligible") is not True
            or inspection.get("run_id") != run_id
            or inspection.get("suite_id") != report.get("suite_id")
            or inspection.get("stage") != "formal_acceptance"
        ):
            raise ValueError(f"run terminal evidence is inconsistent: {run_id}")
        events = _trace_events(snapshots["trace"])
        commands = _trace_commands(events)
        if commands != list(report_row.get("commands") or []):
            raise ValueError(f"run command evidence differs from suite report: {run_id}")
        expected_paths = tuple(
            str(path)
            for path in (report.get("configuration") or {}).get("expected_memory_paths", [])
        )
        injection_events = [event for event in events if event.get("event") == "working_memory_injected"]
        if role == "source":
            captured = [event for event in events if event.get("event") == "memory_reference_captured"]
            captured_paths = {
                str((event.get("reference") or {}).get("path") or "")
                for event in captured
                if isinstance(event.get("reference"), Mapping)
            }
            if captured_paths != set(expected_paths) or "working_memory" not in artifacts:
                raise ValueError(f"source run has no exact grounded memory evidence: {run_id}")
        elif role == "m0":
            if injection_events:
                raise ValueError(f"M0 run unexpectedly injected working memory: {run_id}")
        else:
            if len(injection_events) != 1 or injection_events[0].get("source_run_id") != paired_source_id:
                raise ValueError(f"M1 run injected the wrong source memory: {run_id}")
            injected_paths = {
                str(reference.get("path") or "")
                for reference in injection_events[0].get("references", [])
                if isinstance(reference, Mapping)
            }
            if injected_paths != set(expected_paths):
                raise ValueError(f"M1 run injected unrelated memory: {run_id}")
        if role in {"m0", "m1"} and _count_expected_reads(commands, expected_paths) != _integer(
            report_row.get("repeated_source_file_reads")
        ):
            raise ValueError(f"run repeated-read evidence differs from suite report: {run_id}")
        totals["run_count"] += 1
        totals["provider_errors"] += _integer(metrics.get("provider_errors"))
        totals["provider_retried_requests"] += _integer(metrics.get("provider_retried_requests"))
        totals["protocol_errors"] += _integer(metrics.get("protocol_errors"))
        totals["waiting_approval_runs"] += int(state.get("status") == "waiting_approval")
        models.update(str(model) for model in metrics.get("provider_response_models", []) if model)
    totals["provider_response_models"] = sorted(models)
    return totals


def _trace_events(data: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _trace_commands(events: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        str(action.get("command") or "")
        for event in events
        if event.get("event") == "action_parsed"
        and isinstance((action := event.get("action")), Mapping)
        and action.get("type") == "bash"
    ]


def _count_expected_reads(commands: Sequence[str], paths: Sequence[str]) -> int:
    return sum(
        1
        for command in commands
        if _is_read_command(command)
        and any(path in command.replace("\\", "/") for path in paths)
    )


def _is_read_command(command: str) -> bool:
    lowered = command.lower()
    return bool(
        re.search(r"(^|[;&|\r\n]\s*)(cat|head|tail|sed|rg|grep|find|fd)(\.exe)?\b", lowered)
        or "get-content" in lowered
        or "select-string" in lowered
    )


def _canonical_profile(case_name: str, configuration: Mapping[str, Any]) -> bool:
    expected = REQUIRED_MEMORY_CASES.get(case_name)
    profiles = configuration.get("case_authority_profiles")
    if expected is None or not isinstance(profiles, Mapping) or len(profiles) != 1:
        return False
    profile = next(iter(profiles.values()))
    return isinstance(profile, Mapping) and (
        profile.get("source_path"),
        profile.get("fixture_source_path"),
    ) == expected


def _independent_ids(rows: Sequence[Mapping[str, Any]]) -> bool:
    suite_ids = [str(row.get("suite_id") or "") for row in rows]
    run_ids = [
        str(run.get("run_id") or "")
        for row in rows
        for attempt in row.get("attempts", [])
        if isinstance(attempt, Mapping)
        for run in (attempt.get("source") or {}, attempt.get("m0") or {}, attempt.get("m1") or {})
    ]
    return len(set(suite_ids)) == len(suite_ids) == 3 and len(set(run_ids)) == len(run_ids) == 27


def _runtime(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("_verified_runtime")
    return dict(value) if isinstance(value, Mapping) else {}


def _accuracy(rows: Sequence[Mapping[str, Any]]) -> float:
    return sum(row.get("key_facts_correct") is True for row in rows) / len(rows) if rows else 0.0


def _source_reference(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("suite_id") or ""),
        "path": str(row.get("_evidence_source_path") or ""),
        "report_sha256": str(row.get("_evidence_report_sha256") or ""),
        "manifest_sha256": str(row.get("_evidence_manifest_sha256") or ""),
    }


def _artifact_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _embedded_source(source: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    report_path = Path(str(source.get("path") or "")).resolve()
    manifest_path = report_path.parent / "manifest.json"
    try:
        report_bytes = report_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot embed working-memory source evidence: {source.get('id') or index}") from exc
    if (
        hashlib.sha256(report_bytes).hexdigest() != source.get("report_sha256")
        or hashlib.sha256(manifest_bytes).hexdigest() != source.get("manifest_sha256")
    ):
        raise ValueError(f"working-memory source changed before archive: {source.get('id') or index}")
    return {
        "index": index,
        "id": str(source.get("id") or ""),
        "origin_path": str(report_path),
        "report_sha256": source.get("report_sha256"),
        "manifest_sha256": source.get("manifest_sha256"),
        "report": json.loads(report_bytes),
        "manifest": json.loads(manifest_bytes),
    }


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
