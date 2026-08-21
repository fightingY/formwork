from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from minicc.core.context import CompactionStrategy
from minicc.evals.assertions import (
    assert_trace_action_shape_events,
    trace_action_shape_evidence_events,
)
from minicc.evals.cache_probe_runner import (
    fixed_probe_request_sha256s,
    fixed_probe_sequence_sha256,
)
REQUIRED_REAL_CASE = "C02_fix_failing_test"
REQUIRED_INPUT_MILESTONE = "v2.1.1-development"


@dataclass(frozen=True)
class CacheABBundle:
    json_path: Path
    markdown_path: Path


CacheABRound = tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
]


def build_cache_ab_report(
    rounds: Sequence[CacheABRound],
    *,
    required_rounds: int = 2,
    minimum_probe_requests: int = 5,
    minimum_real_attempts: int = 3,
) -> dict[str, Any]:
    if not rounds:
        raise ValueError("at least one P0/P1 cache evidence round is required")
    if required_rounds < 1:
        raise ValueError("required_rounds must be at least 1")

    round_reports = [
        _build_round(
            index,
            p0_probe,
            p1_probe,
            p0_suite,
            p1_suite,
            minimum_probe_requests=minimum_probe_requests,
            minimum_real_attempts=minimum_real_attempts,
        )
        for index, (p0_probe, p1_probe, p0_suite, p1_suite) in enumerate(rounds, start=1)
    ]
    evidence_ids = [
        evidence_id
        for round_ in round_reports
        for evidence_id in (
            round_["p0_probe_id"],
            round_["p1_probe_id"],
            round_["p0_suite_id"],
            round_["p1_suite_id"],
        )
    ]
    independent_evidence = bool(evidence_ids) and all(evidence_ids) and len(set(evidence_ids)) == len(
        evidence_ids
    )
    sequence_ids = [str(round_.get("cache_sequence_id") or "") for round_ in round_reports]
    independent_sequence_ids = (
        bool(sequence_ids)
        and all(sequence_ids)
        and len(set(sequence_ids)) == len(sequence_ids)
    )
    sequence_shapes = {
        re.sub(r"\d+", "#", sequence_id)
        for sequence_id in sequence_ids
        if sequence_id
    }
    sequence_shape_consistent = len(sequence_shapes) == 1
    dynamic_sequence_hashes = [
        str(round_.get("dynamic_sequence_sha256") or "")
        for round_ in round_reports
    ]
    independent_dynamic_sequences = (
        bool(dynamic_sequence_hashes)
        and all(dynamic_sequence_hashes)
        and len(set(dynamic_sequence_hashes)) == len(dynamic_sequence_hashes)
    )
    execution_orders = [str(round_.get("execution_order") or "") for round_ in round_reports]
    balanced_execution_order = (
        len(round_reports) == required_rounds
        and set(execution_orders) == {"p0-first", "p1-first"}
        and all(round_.get("execution_order_verified") for round_ in round_reports)
    )
    run_ids = [
        str(case.get("run_id") or "")
        for round_ in rounds
        for suite in (round_[2], round_[3])
        for case in suite.get("cases", [])
        if isinstance(case, Mapping)
    ]
    independent_run_ids = bool(run_ids) and all(run_ids) and len(set(run_ids)) == len(run_ids)
    global_errors = [
        *_global_comparability_errors(rounds),
        *_global_runtime_identity_errors(round_reports),
    ]
    enough_rounds = len(round_reports) >= required_rounds
    same_direction = all(
        round_["criteria"]["fixed_cache_improved"]
        and round_["criteria"]["fixed_uncached_tokens_lower"]
        and round_["criteria"]["real_cache_not_lower"]
        for round_ in round_reports
    )
    passed = (
        enough_rounds
        and independent_evidence
        and independent_sequence_ids
        and sequence_shape_consistent
        and independent_dynamic_sequences
        and balanced_execution_order
        and independent_run_ids
        and not global_errors
        and same_direction
        and all(round_["passed"] for round_ in round_reports)
    )
    structural_ok = (
        independent_evidence
        and independent_sequence_ids
        and sequence_shape_consistent
        and independent_dynamic_sequences
        and all(round_.get("execution_order_verified") for round_ in round_reports)
        and independent_run_ids
        and not global_errors
        and all(round_["structural_passed"] for round_ in round_reports)
    )
    inconclusive = structural_ok and (
        not enough_rounds or any(not round_["conclusive"] for round_ in round_reports)
    )
    return {
        "schema_version": 1,
        "entity_type": "prompt_cache_ab_report",
        "milestone": "v2.1.1",
        "status": "PASS" if passed else "INCONCLUSIVE" if inconclusive else "FAIL",
        "passed": passed,
        "required_rounds": required_rounds,
        "completed_rounds": len(round_reports),
        "minimum_probe_requests": minimum_probe_requests,
        "minimum_real_attempts": minimum_real_attempts,
        "independent_evidence": independent_evidence,
        "independent_sequence_ids": independent_sequence_ids,
        "sequence_shape_consistent": sequence_shape_consistent,
        "independent_dynamic_sequences": independent_dynamic_sequences,
        "balanced_execution_order": balanced_execution_order,
        "independent_run_ids": independent_run_ids,
        "same_direction": same_direction,
        "global_comparability_errors": global_errors,
        "rounds": round_reports,
    }


def write_cache_ab_report(report: Mapping[str, Any], output_dir: Path) -> CacheABBundle:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Prompt Cache A/B report already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        (temporary / "report.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (temporary / "report.md").write_text(
            format_cache_ab_markdown(report),
            encoding="utf-8",
        )
        temporary.replace(output_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return CacheABBundle(
        json_path=output_dir / "report.json",
        markdown_path=output_dir / "report.md",
    )


def load_suite_report(
    path: Path,
    *,
    verify_manifest: bool = False,
) -> dict[str, Any]:
    report_bytes = path.read_bytes()
    payload = json.loads(report_bytes.decode("utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("entity_type") != "suite_report"
        or not isinstance(payload.get("cases"), list)
    ):
        raise ValueError(f"not an eval suite report: {path}")
    if verify_manifest:
        manifest_bytes = _verify_suite_manifest(
            path,
            payload,
            report_bytes=report_bytes,
        )
        payload["_evidence_integrity_verified"] = True
        payload["_evidence_source_path"] = str(path.resolve())
        payload["_evidence_report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
        payload["_evidence_manifest_sha256"] = hashlib.sha256(
            manifest_bytes
        ).hexdigest()
    return payload


def _verify_suite_manifest(
    path: Path,
    report: Mapping[str, Any],
    *,
    report_bytes: bytes,
) -> bytes:
    manifest_path = path.parent / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid suite manifest: {manifest_path}") from exc
    cases = report.get("cases", [])
    if not all(isinstance(case, Mapping) for case in cases):
        raise ValueError(f"suite report has invalid case records: {path}")
    case_started_at = [
        _timestamp((case.get("metrics") or {}).get("started_at"))
        for case in cases
    ]
    case_completed_at = [
        _timestamp((case.get("metrics") or {}).get("completed_at"))
        for case in cases
    ]
    suite_created_at = _timestamp(report.get("created_at"))
    suite_completed_at = _timestamp(report.get("completed_at"))
    if (
        not cases
        or suite_created_at is None
        or suite_completed_at is None
        or any(value is None for value in case_started_at)
        or any(value is None for value in case_completed_at)
        or suite_created_at > min(value for value in case_started_at if value is not None)
        or suite_completed_at != max(value for value in case_completed_at if value is not None)
    ):
        raise ValueError(f"suite timestamps do not match run evidence: {path}")
    report_run_ids = [str(case.get("run_id") or "") for case in cases]
    if (
        not isinstance(manifest, dict)
        or manifest.get("entity_type") != "suite"
        or manifest.get("suite_id") != report.get("suite_id")
        or path.parent.name != report.get("suite_id")
        or manifest.get("milestone") != report.get("milestone")
        or manifest.get("stage") != report.get("stage")
        or manifest.get("created_at") != report.get("created_at")
        or manifest.get("completed_at") != report.get("completed_at")
        or manifest.get("result") != report.get("result")
        or manifest.get("configuration") != report.get("configuration")
        or list(manifest.get("run_ids") or []) != report_run_ids
        or not report_run_ids
        or not all(report_run_ids)
        or len(set(report_run_ids)) != len(report_run_ids)
    ):
        raise ValueError(f"suite manifest does not match report: {path}")
    artifacts = manifest.get("artifacts")
    expected_suite_artifacts = {
        "report_json": "report.json",
        "report_markdown": "report.md",
        "report_csv": "report.csv",
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(expected_suite_artifacts):
        raise ValueError(f"suite manifest has incomplete report hashes: {manifest_path}")
    for name, relative in expected_suite_artifacts.items():
        _verify_artifact_hash(
            artifacts[name],
            path.parent / relative,
            expected_path=relative,
            label=f"suite {name}",
            artifact_bytes=report_bytes if name == "report_json" else None,
        )
    required_evidence = {
        "state",
        "trace",
        "metrics",
        "workspace_manifest",
        "diff",
        "run_report",
        "suite_manifest",
    }
    for case in cases:
        if (
            case.get("suite_id") != report.get("suite_id")
            or case.get("milestone") != report.get("milestone")
            or case.get("stage") != report.get("stage")
        ):
            raise ValueError(
                f"suite case identity does not match report: {case.get('run_id')}"
            )
        evidence = case.get("evidence")
        if not isinstance(evidence, Mapping) or not required_evidence.issubset(evidence):
            raise ValueError(f"suite run evidence is incomplete: {case.get('run_id')}")
        for key in required_evidence - {"suite_manifest"}:
            if not Path(str(evidence[key])).is_file():
                raise ValueError(f"suite run evidence is missing: {evidence[key]}")
        suite_manifest_path = Path(str(evidence["suite_manifest"])).resolve()
        if suite_manifest_path != manifest_path.resolve():
            raise ValueError(
                f"suite run points to a different manifest: {suite_manifest_path}"
            )
        verified_rows = _verify_run_artifact_index(
            case,
            evidence,
            configuration=report.get("configuration") or {},
        )
        if not isinstance(case, dict):
            raise ValueError(f"suite case record is not mutable: {case.get('run_id')}")
        if "request_rows" not in case:
            case["request_rows"] = verified_rows
    return manifest_bytes


def _verify_run_artifact_index(
    case: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    configuration: Mapping[str, Any],
) -> list[dict[str, Any]]:
    run_id = str(case.get("run_id") or "")
    run_report_path = Path(str(evidence["run_report"])).resolve()
    run_dir = run_report_path.parent
    index_path = run_dir.parent.parent / "artifacts" / run_id / "manifest.json"
    try:
        index = json.loads(index_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid run artifact index or report: {run_id}") from exc
    normalized_evidence = {
        name: str(Path(str(value)).resolve())
        for name, value in evidence.items()
    }
    if (
        not isinstance(index, Mapping)
        or index.get("entity_type") != "artifact_index"
        or index.get("run_id") != run_id
        or Path(str(index.get("run_dir") or "")).resolve() != run_dir
        or index.get("evidence") != normalized_evidence
    ):
        raise ValueError(f"run artifact index does not match suite report: {index_path}")
    required_hashes = {
        "state",
        "trace",
        "metrics",
        "workspace_manifest",
        "diff",
        "run_report",
    }
    indexed_artifacts = index.get("artifacts")
    if (
        not isinstance(indexed_artifacts, Mapping)
        or not required_hashes.issubset(indexed_artifacts)
    ):
        raise ValueError(f"run artifact index has incomplete hashes: {index_path}")
    artifact_snapshots: dict[str, bytes] = {}
    try:
        for name in required_hashes:
            artifact_path = Path(normalized_evidence[name])
            data = artifact_path.read_bytes()
            _verify_artifact_hash(
                indexed_artifacts[name],
                artifact_path,
                expected_path=str(artifact_path),
                label=f"run {run_id} {name}",
                artifact_bytes=data,
            )
            artifact_snapshots[name] = data
        run_report = json.loads(artifact_snapshots["run_report"].decode("utf-8"))
        metrics = json.loads(artifact_snapshots["metrics"].decode("utf-8"))
        state = json.loads(artifact_snapshots["state"].decode("utf-8"))
        workspace_manifest = json.loads(
            artifact_snapshots["workspace_manifest"].decode("utf-8")
        )
        trace_events = _trace_events_from_bytes(artifact_snapshots["trace"])
        trace_request_rows = [
            event
            for event in trace_events
            if event.get("event") == "model_response"
        ]
        trace_assertion_events = trace_action_shape_evidence_events(
            trace_events
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid hashed run artifact: {run_id}") from exc
    metrics_metadata_keys = {
        "schema_version",
        "run_id",
        "suite_id",
        "milestone",
        "stage",
        "status",
        "final_answer_present",
    }
    state_metrics = {
        key: value
        for key, value in metrics.items()
        if key not in metrics_metadata_keys
    } if isinstance(metrics, Mapping) else {}
    case_source_path = str(case.get("case_source_path") or "")
    fixture_source_path = str(case.get("fixture_source_path") or "")
    authority_required = "2.1.2" in str(case.get("milestone") or "")
    case_request_rows = case.get("request_rows")
    if (
        authority_required
        and not isinstance(case_request_rows, list)
    ) or (
        isinstance(case_request_rows, list)
        and (
            case_request_rows != trace_request_rows
            or not isinstance(run_report, Mapping)
            or run_report.get("request_rows") != case_request_rows
        )
    ):
        raise ValueError(
            f"run request rows do not match hashed trace: {run_report_path}"
        )
    case_trace_assertion_events = case.get("trace_assertion_events")
    portable_action_evidence_required = (
        "2.1.2" in str(case.get("milestone") or "")
        and configuration.get("release_gate") is True
    )
    if (
        portable_action_evidence_required
        and not isinstance(case_trace_assertion_events, list)
    ) or (
        isinstance(case_trace_assertion_events, list)
        and (
            case_trace_assertion_events != trace_assertion_events
            or not isinstance(run_report, Mapping)
            or run_report.get("trace_assertion_events")
            != case_trace_assertion_events
        )
    ):
        raise ValueError(
            "run trace assertion evidence does not match hashed trace: "
            f"{run_report_path}"
        )
    assertion_specs = case.get("assertion_specs")
    assertion_results = case.get("assertions")
    if (
        authority_required
        and (
            not isinstance(assertion_specs, list)
            or not isinstance(assertion_results, list)
        )
    ):
        raise ValueError(
            f"run assertion specs are missing: {run_report_path}"
        )
    if isinstance(assertion_specs, list) and isinstance(assertion_results, list):
        if (
            not isinstance(run_report, Mapping)
            or run_report.get("assertion_specs") != assertion_specs
            or len(assertion_specs) != len(assertion_results)
        ):
            raise ValueError(
                f"run assertion specs do not match suite report: {run_report_path}"
            )
        for spec, stored_result in zip(
            assertion_specs,
            assertion_results,
            strict=True,
        ):
            if (
                not isinstance(spec, dict)
                or spec.get("type") != "trace_action_shape"
            ):
                continue
            replayed = asdict(
                assert_trace_action_shape_events(
                    spec,
                    trace_assertion_events,
                )
            )
            if replayed != stored_result:
                raise ValueError(
                    "run action shape does not match hashed trace: "
                    f"{run_report_path}"
                )
    if authority_required:
        project_root = run_dir.parents[2]
        expected_fixture_root = (
            Path(fixture_source_path.removeprefix("external:")).resolve()
            if fixture_source_path.startswith("external:")
            else (project_root / fixture_source_path).resolve()
        )
        if (
            not case_source_path
            or not fixture_source_path
            or not isinstance(run_report, Mapping)
            or not isinstance(workspace_manifest, Mapping)
            or workspace_manifest.get("run_id") != run_id
            or Path(str(workspace_manifest.get("source_root") or "")).resolve()
            != expected_fixture_root
            or run_report.get("case_source_path") != case_source_path
            or run_report.get("fixture_source_path") != fixture_source_path
            or Path(str(run_report.get("workspace_manifest") or "")).resolve()
            != Path(str(evidence["workspace_manifest"])).resolve()
        ):
            raise ValueError(
                f"run source paths do not match suite report: {run_report_path}"
            )
    if (
        not isinstance(run_report, Mapping)
        or not isinstance(metrics, Mapping)
        or not isinstance(state, Mapping)
        or run_report.get("run_id") != run_id
        or run_report.get("suite_id") != case.get("suite_id")
        or run_report.get("name") != case.get("name")
        or run_report.get("attempt") != case.get("attempt")
        or run_report.get("milestone") != case.get("milestone")
        or run_report.get("stage") != case.get("stage")
        or run_report.get("run_status") != case.get("run_status")
        or run_report.get("passed") is not case.get("passed")
        or run_report.get("task_success") is not case.get("task_success")
        or run_report.get("agent_success") is not case.get("agent_success")
        or run_report.get("infrastructure_success") is not case.get("infrastructure_success")
        or run_report.get("policy_outcome") != case.get("policy_outcome")
        or run_report.get("evidence") != evidence
        or run_report.get("assertions") != case.get("assertions")
        or run_report.get("metrics") != case.get("metrics")
        or metrics != case.get("metrics")
        or run_report.get("source_commit") != configuration.get("git_commit")
        or (run_report.get("provider") or {}).get("base_url")
        != configuration.get("base_url")
        or (run_report.get("provider") or {}).get("model")
        != configuration.get("model")
        or (run_report.get("provider") or {}).get("temperature")
        != configuration.get("temperature")
        or (run_report.get("sandbox") or {}).get("mode")
        != case.get("sandbox_mode")
        or (run_report.get("sandbox") or {}).get("image")
        != configuration.get("docker_image")
        or state.get("run_id") != run_id
        or state.get("suite_id") != case.get("suite_id")
        or state.get("milestone") != case.get("milestone")
        or state.get("stage") != case.get("stage")
        or state.get("status") != case.get("run_status")
        or state.get("metrics") != state_metrics
        or metrics.get("run_id") != run_id
        or metrics.get("suite_id") != case.get("suite_id")
        or metrics.get("milestone") != case.get("milestone")
        or metrics.get("stage") != case.get("stage")
        or metrics.get("status") != case.get("run_status")
        or metrics.get("prompt_layout") != configuration.get("prompt_layout")
        or not _prompt_namespace_matches(configuration, state)
    ):
        raise ValueError(f"run report does not match suite report: {run_report_path}")
    expected_formal_eligibility = (
        case.get("stage") == "formal_acceptance"
        and case.get("run_status") in {"completed", "failed"}
    )
    if case.get("formal_metric_eligible") is not expected_formal_eligibility:
        raise ValueError(f"run formal metric eligibility is inconsistent: {run_id}")
    return trace_request_rows


def _prompt_namespace_matches(
    configuration: Mapping[str, Any], state: Mapping[str, Any]
) -> bool:
    sequence_id = configuration.get("cache_sequence_id")
    if sequence_id is None:
        return True
    return state.get("prompt_namespace") == f"cache-experiment/{sequence_id}"


def _trace_events_from_bytes(data: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("trace event must be a JSON object")
        events.append(event)
    return events


def _verify_artifact_hash(
    entry: Any,
    artifact_path: Path,
    *,
    expected_path: str,
    label: str,
    artifact_bytes: bytes | None = None,
) -> None:
    if not isinstance(entry, Mapping):
        raise ValueError(f"{label} has no artifact hash")
    if str(entry.get("path") or "") != expected_path or not artifact_path.is_file():
        raise ValueError(f"{label} artifact path mismatch: {artifact_path}")
    data = artifact_path.read_bytes() if artifact_bytes is None else artifact_bytes
    if (
        _integer(entry.get("bytes")) != len(data)
        or str(entry.get("sha256") or "") != hashlib.sha256(data).hexdigest()
    ):
        raise ValueError(f"{label} artifact hash mismatch: {artifact_path}")


def format_cache_ab_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# miniCC V2.1.1 Prompt Cache P0/P1 A/B",
        "",
        f"Status: **{report['status']}**",
        f"Independent rounds: {report['completed_rounds']}/{report['required_rounds']}",
        f"Unique immutable evidence: {'yes' if report['independent_evidence'] else 'no'}",
        f"Unique run evidence: {'yes' if report['independent_run_ids'] else 'no'}",
        f"Unique round namespaces: {'yes' if report['independent_sequence_ids'] else 'no'}",
        f"Balanced verified execution order: {'yes' if report['balanced_execution_order'] else 'no'}",
        f"Same-direction cache improvement: {'yes' if report['same_direction'] else 'no'}",
        "",
    ]
    for error in report["global_comparability_errors"]:
        lines.append(f"- Global comparison error: {error}")
    if report["global_comparability_errors"]:
        lines.append("")

    for round_ in report["rounds"]:
        lines.extend(
            [
                f"## Round {round_['round']}: "
                f"{'PASS' if round_['passed'] else ('INCONCLUSIVE' if not round_['conclusive'] else 'FAIL')}",
                "",
                f"- P0 fixed probe: `{round_['p0_probe_id']}`",
                f"- P1 fixed probe: `{round_['p1_probe_id']}`",
                f"- P0 real-case suite: `{round_['p0_suite_id']}`",
                f"- P1 real-case suite: `{round_['p1_suite_id']}`",
                f"- Namespace/order: `{round_['cache_sequence_id']}` / `{round_['execution_order']}` "
                f"({'verified' if round_['execution_order_verified'] else 'not verified'})",
                f"- Stable prefix hash: P0 fixed=`{round_['p0']['fixed']['stable_prefix'].get('sha256') or 'missing'}`, "
                f"P1 fixed=`{round_['p1']['fixed']['stable_prefix'].get('sha256') or 'missing'}`",
                f"- Stable prefix hash: P0 real=`{round_['p0']['real']['stable_prefix'].get('sha256') or 'missing'}`, "
                f"P1 real=`{round_['p1']['real']['stable_prefix'].get('sha256') or 'missing'}`",
                "",
                "| Workload | Variant | Requests | Reported | Unreported | Hit tokens | "
                "Miss tokens | Weighted hit rate | Prompt tokens | Latency total/mean ms | "
                "Task pass | Stable prefix est. tokens |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                _workload_row("fixed/all", "P0", round_["p0"]["fixed"]["cache"], round_["p0"]["fixed"]),
                _workload_row("fixed/all", "P1", round_["p1"]["fixed"]["cache"], round_["p1"]["fixed"]),
                _workload_row(
                    "fixed/steady",
                    "P0",
                    round_["p0"]["fixed"]["steady_state_cache"],
                    round_["p0"]["fixed"],
                ),
                _workload_row(
                    "fixed/steady",
                    "P1",
                    round_["p1"]["fixed"]["steady_state_cache"],
                    round_["p1"]["fixed"],
                ),
                _workload_row("real", "P0", round_["p0"]["real"]["cache"], round_["p0"]["real"]),
                _workload_row("real", "P1", round_["p1"]["real"]["cache"], round_["p1"]["real"]),
                "",
                "Actual improvement: "
                f"fixed_rate_delta={_format_delta(round_['improvement']['fixed_weighted_hit_rate_delta'])}, "
                f"fixed_hit_delta={round_['improvement']['fixed_hit_tokens_delta']}, "
                f"real_rate_delta={_format_delta(round_['improvement']['real_weighted_hit_rate_delta'])}, "
                f"real_hit_delta={round_['improvement']['real_hit_tokens_delta']}, "
                f"combined_rate_delta={_format_delta(round_['improvement']['combined_weighted_hit_rate_delta'])}.",
                "",
            ]
        )
        lines.extend(
            [
                "### Fixed probe request detail",
                "",
                "| Variant | # | Request | Task | Attempts | Cache | Prompt | Hit | Miss | "
                "Hit rate | Latency ms | Request SHA-256 |",
                "|---|---:|---|---|---:|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for variant in ("p0", "p1"):
            lines.extend(
                _fixed_request_row(variant.upper(), request)
                for request in round_[variant]["fixed"]["requests"]
            )
        lines.extend(
            [
                "",
                "### Real C02 run detail",
                "",
                "| Variant | Attempt | Run ID | Pass | Task | Agent | Infra | Requests | "
                "Prompt | Hit | Miss | Latency ms | Provider attempts | Retries | Layout |",
                "|---|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for variant in ("p0", "p1"):
            lines.extend(
                _real_run_row(variant.upper(), run)
                for run in round_[variant]["real"]["run_rows"]
            )
        lines.append("")
        for name, criterion_passed in round_["criteria"].items():
            lines.append(f"- {'PASS' if criterion_passed else 'FAIL'} `{name}`")
        for error in round_["comparability_errors"]:
            lines.append(f"- Comparison error: {error}")
        lines.append("")
    return "\n".join(lines)


def _build_round(
    index: int,
    p0_probe: Mapping[str, Any],
    p1_probe: Mapping[str, Any],
    p0_suite: Mapping[str, Any],
    p1_suite: Mapping[str, Any],
    *,
    minimum_probe_requests: int,
    minimum_real_attempts: int,
) -> dict[str, Any]:
    _validate_variant(p0_probe, "p0", kind="probe")
    _validate_variant(p1_probe, "p1", kind="probe")
    _validate_variant(p0_suite, "p0", kind="suite")
    _validate_variant(p1_suite, "p1", kind="suite")

    p0_fixed = _fixed_probe_metrics(p0_probe)
    p1_fixed = _fixed_probe_metrics(p1_probe)
    p0_real = _real_suite_metrics(p0_suite)
    p1_real = _real_suite_metrics(p1_suite)
    comparability_errors = [
        *_required_configuration_errors(p0_probe, kind="fixed probe P0"),
        *_required_configuration_errors(p1_probe, kind="fixed probe P1"),
        *_required_configuration_errors(p0_suite, kind="real suite P0"),
        *_required_configuration_errors(p1_suite, kind="real suite P1"),
        *_pair_configuration_errors(p0_probe, p1_probe, label="fixed probe"),
        *_pair_configuration_errors(p0_suite, p1_suite, label="real suite"),
        *_cross_workload_errors(p0_probe, p1_probe, p0_suite, p1_suite),
        *_runtime_identity_errors(p0_fixed, p1_fixed, p0_real, p1_real),
    ]
    configurations = [
        dict(payload.get("configuration") or {})
        for payload in (p0_probe, p1_probe, p0_suite, p1_suite)
    ]
    sequence_values = {
        str(config.get("cache_sequence_id") or "")
        for config in configurations
    }
    cache_sequence_id = next(iter(sequence_values)) if len(sequence_values) == 1 else ""
    order_values = {
        str(config.get("execution_order") or "")
        for config in configurations
    }
    execution_order = next(iter(order_values)) if len(order_values) == 1 else ""
    execution_order_verified = _execution_order_verified(
        execution_order,
        p0_probe=p0_probe,
        p1_probe=p1_probe,
        p0_suite=p0_suite,
        p1_suite=p1_suite,
    )
    dynamic_sequence_values = {
        str((probe.get("configuration") or {}).get("dynamic_sequence_sha256") or "")
        for probe in (p0_probe, p1_probe)
    }
    dynamic_sequence_sha256 = (
        next(iter(dynamic_sequence_values))
        if len(dynamic_sequence_values) == 1
        else ""
    )
    try:
        expected_dynamic_sequence_sha256 = fixed_probe_sequence_sha256(
            minimum_probe_requests,
            cache_sequence_id,
        )
    except ValueError:
        expected_dynamic_sequence_sha256 = ""
    dynamic_sequence_verified = (
        bool(expected_dynamic_sequence_sha256)
        and dynamic_sequence_sha256 == expected_dynamic_sequence_sha256
    )
    try:
        expected_p0_request_hashes = fixed_probe_request_sha256s(
            "p0",
            minimum_probe_requests,
            cache_sequence_id,
            recent_turns=_integer(configurations[0].get("recent_turns")),
            max_prompt_chars=_integer(configurations[0].get("max_prompt_chars")),
            compaction_strategy=cast(
                CompactionStrategy, str(configurations[0].get("compaction_strategy") or "")
            ),
        )
        expected_p1_request_hashes = fixed_probe_request_sha256s(
            "p1",
            minimum_probe_requests,
            cache_sequence_id,
            recent_turns=_integer(configurations[1].get("recent_turns")),
            max_prompt_chars=_integer(configurations[1].get("max_prompt_chars")),
            compaction_strategy=cast(
                CompactionStrategy, str(configurations[1].get("compaction_strategy") or "")
            ),
        )
    except ValueError:
        expected_p0_request_hashes = []
        expected_p1_request_hashes = []
    fixed_request_payloads_verified = (
        p0_fixed["request_hashes"] == expected_p0_request_hashes
        and p1_fixed["request_hashes"] == expected_p1_request_hashes
    )
    p0_matrix = _case_matrix(p0_suite)
    p1_matrix = _case_matrix(p1_suite)
    expected_case_matrix = [
        (REQUIRED_REAL_CASE, attempt)
        for attempt in range(1, minimum_real_attempts + 1)
    ]
    real_case_matrix_ok = (
        p0_matrix == expected_case_matrix
        and p1_matrix == expected_case_matrix
    )
    expected_request_indices = list(range(1, minimum_probe_requests + 1))
    fixed_request_matrix_ok = (
        _request_indices(p0_probe) == expected_request_indices
        and _request_indices(p1_probe) == expected_request_indices
    )
    fixed_counts_ok = (
        p0_fixed["request_count"] == minimum_probe_requests
        and p1_fixed["request_count"] == minimum_probe_requests
    )
    fixed_warmup_ok = (
        p0_fixed["warmup_requests"] == 2
        and p1_fixed["warmup_requests"] == 2
        and p0_fixed["steady_state_request_count"] == minimum_probe_requests - 2
        and p1_fixed["steady_state_request_count"] == minimum_probe_requests - 2
    )
    cache_complete = all(
        metrics["coverage_status"] == "complete"
        for metrics in (
            p0_fixed["cache"],
            p1_fixed["cache"],
            p0_fixed["steady_state_cache"],
            p1_fixed["steady_state_cache"],
            p0_real["cache"],
            p1_real["cache"],
        )
    )
    fixed_improved = _cache_improved(
        p0_fixed["steady_state_cache"],
        p1_fixed["steady_state_cache"],
    )
    fixed_uncached_lower = (
        _integer(p1_fixed["steady_state_cache"].get("miss_tokens"))
        < _integer(p0_fixed["steady_state_cache"].get("miss_tokens"))
    )
    fixed_prompt_not_larger = _fixed_prompt_not_larger(p0_probe, p1_probe)
    real_prompt_not_larger = (
        _integer(p1_real["cache"].get("prompt_tokens"))
        <= _integer(p0_real["cache"].get("prompt_tokens"))
    )
    real_prompt_metrics_complete = (
        p0_real["prompt_metrics_complete"]
        and p1_real["prompt_metrics_complete"]
    )
    latency_metrics_complete = (
        p0_fixed["latency_metrics_complete"]
        and p1_fixed["latency_metrics_complete"]
        and p0_real["latency_metrics_complete"]
        and p1_real["latency_metrics_complete"]
    )
    real_not_lower = _cache_not_lower(p0_real["cache"], p1_real["cache"])
    combined_p0 = _combine_cache(p0_fixed["steady_state_cache"], p0_real["cache"])
    combined_p1 = _combine_cache(p1_fixed["steady_state_cache"], p1_real["cache"])
    stable_prefix_ok = _stable_prefix_not_lower(
        p0_fixed,
        p1_fixed,
    ) and _stable_prefix_not_lower(p0_real, p1_real)
    formal_payloads = (p0_probe, p1_probe, p0_suite, p1_suite)
    formal_evidence = (
        p0_probe.get("stage") == "formal_acceptance"
        and p1_probe.get("stage") == "formal_acceptance"
        and p0_suite.get("stage") == "formal_acceptance"
        and p1_suite.get("stage") == "formal_acceptance"
        and all(bool(case.get("formal_metric_eligible")) for case in p0_suite.get("cases", []))
        and all(bool(case.get("formal_metric_eligible")) for case in p1_suite.get("cases", []))
        and all(bool((payload.get("configuration") or {}).get("release_gate")) for payload in formal_payloads)
        and all(
            (payload.get("configuration") or {}).get("worktree_dirty") is False
            for payload in formal_payloads
        )
        and all(
            payload.get("_evidence_integrity_verified") is True
            for payload in formal_payloads
        )
        and all(
            (payload.get("configuration") or {}).get("feedback_memory_mode")
            == "disabled"
            for payload in formal_payloads
        )
        and all(
            payload.get("milestone") == REQUIRED_INPUT_MILESTONE
            and (payload.get("configuration") or {}).get("milestone")
            == REQUIRED_INPUT_MILESTONE
            for payload in formal_payloads
        )
    )
    formal_runtime = all(
        (suite.get("configuration") or {}).get("execute_local") is False
        and (suite.get("configuration") or {}).get("sandbox_mode") == "locked"
        and "@sha256:" in str(
            (suite.get("configuration") or {}).get("docker_image") or ""
        )
        and all(
            isinstance(case, Mapping)
            and case.get("sandbox_mode") == "locked"
            for case in suite.get("cases", [])
        )
        for suite in (p0_suite, p1_suite)
    )
    p0_all_pass = _real_suite_all_passed(p0_suite, p0_real)
    p1_all_pass = _real_suite_all_passed(p1_suite, p1_real)
    no_retries = (
        p0_fixed["retried_requests"] == 0
        and p1_fixed["retried_requests"] == 0
        and p0_real["retried_requests"] == 0
        and p1_real["retried_requests"] == 0
        and p0_fixed["attempts_reported"] == p0_fixed["request_count"]
        and p1_fixed["attempts_reported"] == p1_fixed["request_count"]
        and p0_fixed["attempts_exactly_one"]
        and p1_fixed["attempts_exactly_one"]
        and p0_real["provider_request_attempts"] == p0_real["cache"]["request_count"]
        and p1_real["provider_request_attempts"] == p1_real["cache"]["request_count"]
    )
    criteria = {
        "comparable_configuration": not comparability_errors,
        "formal_immutable_evidence": formal_evidence,
        "formal_locked_docker_runtime": formal_runtime,
        "fixed_sequence_exact_request_count": fixed_counts_ok,
        "fixed_sequence_request_indices_locked": fixed_request_matrix_ok,
        "fixed_dynamic_sequence_verified": dynamic_sequence_verified,
        "fixed_request_payloads_unique": (
            p0_fixed["request_hashes_complete_and_unique"]
            and p1_fixed["request_hashes_complete_and_unique"]
        ),
        "fixed_request_payloads_verified": fixed_request_payloads_verified,
        "fixed_warmup_rule_locked": fixed_warmup_ok,
        "fixed_sequence_requests_succeeded": (
            bool(p0_probe.get("passed"))
            and bool(p1_probe.get("passed"))
            and p0_fixed["requests_succeeded"]
            and p1_fixed["requests_succeeded"]
        ),
        "required_real_case_matrix": real_case_matrix_ok,
        "cache_metrics_complete": cache_complete,
        "latency_metrics_complete": latency_metrics_complete,
        "no_retried_provider_requests": no_retries,
        "p0_real_suite_all_passed": p0_all_pass,
        "p1_real_suite_all_passed": p1_all_pass,
        "p1_task_pass_rate_not_lower": p1_real["pass_rate"] >= p0_real["pass_rate"],
        "p1_stable_prefix_not_lower": stable_prefix_ok,
        "fixed_prompt_tokens_not_larger_per_request": fixed_prompt_not_larger,
        "real_prompt_token_metrics_complete": real_prompt_metrics_complete,
        "real_prompt_tokens_not_larger": real_prompt_not_larger,
        "fixed_cache_improved": fixed_improved,
        "fixed_uncached_tokens_lower": fixed_uncached_lower,
        "real_cache_not_lower": real_not_lower,
        "execution_order_verified": execution_order_verified,
    }
    structural_names = {
        "comparable_configuration",
        "formal_immutable_evidence",
        "formal_locked_docker_runtime",
        "fixed_sequence_exact_request_count",
        "fixed_sequence_request_indices_locked",
        "fixed_dynamic_sequence_verified",
        "fixed_request_payloads_unique",
        "fixed_request_payloads_verified",
        "fixed_warmup_rule_locked",
        "fixed_sequence_requests_succeeded",
        "required_real_case_matrix",
        "latency_metrics_complete",
        "no_retried_provider_requests",
        "p0_real_suite_all_passed",
        "p1_real_suite_all_passed",
        "p1_task_pass_rate_not_lower",
        "p1_stable_prefix_not_lower",
        "fixed_prompt_tokens_not_larger_per_request",
        "real_prompt_token_metrics_complete",
        "real_prompt_tokens_not_larger",
        "execution_order_verified",
    }
    structural_passed = all(criteria[name] for name in structural_names)
    conclusive = cache_complete
    passed = conclusive and all(criteria.values())
    return {
        "round": index,
        "p0_probe_id": p0_probe.get("probe_id"),
        "p1_probe_id": p1_probe.get("probe_id"),
        "p0_suite_id": p0_suite.get("suite_id"),
        "p1_suite_id": p1_suite.get("suite_id"),
        "cache_sequence_id": cache_sequence_id,
        "dynamic_sequence_sha256": dynamic_sequence_sha256,
        "execution_order": execution_order,
        "execution_order_verified": execution_order_verified,
        "comparability_errors": comparability_errors,
        "p0": {"fixed": p0_fixed, "real": p0_real, "combined_cache": combined_p0},
        "p1": {"fixed": p1_fixed, "real": p1_real, "combined_cache": combined_p1},
        "improvement": {
            "fixed_weighted_hit_rate_delta": _rate_delta(
                p0_fixed["steady_state_cache"],
                p1_fixed["steady_state_cache"],
            ),
            "fixed_hit_tokens_delta": (
                p1_fixed["steady_state_cache"]["hit_tokens"]
                - p0_fixed["steady_state_cache"]["hit_tokens"]
            ),
            "real_weighted_hit_rate_delta": _rate_delta(p0_real["cache"], p1_real["cache"]),
            "real_hit_tokens_delta": (
                p1_real["cache"]["hit_tokens"] - p0_real["cache"]["hit_tokens"]
            ),
            "combined_weighted_hit_rate_delta": _rate_delta(combined_p0, combined_p1),
            "combined_hit_tokens_delta": combined_p1["hit_tokens"] - combined_p0["hit_tokens"],
        },
        "criteria": criteria,
        "structural_passed": structural_passed,
        "conclusive": conclusive,
        "passed": passed,
    }


def _fixed_probe_metrics(probe: Mapping[str, Any]) -> dict[str, Any]:
    stable = dict(probe.get("stable_prefix") or {})
    requests = [
        request
        for request in probe.get("requests", [])
        if isinstance(request, Mapping)
    ]
    attempts = [
        value
        for request in requests
        if (value := _optional_int(request.get("attempt_count"))) is not None
    ]
    requests_succeeded = bool(requests) and all(
        request.get("request_success") is True
        and request.get("task_success") is True
        for request in requests
    )
    request_hashes = [
        str(request.get("request_sha256") or "")
        for request in requests
    ]
    request_rows = [
        {
            "request_index": _integer(request.get("request_index")),
            "request_success": request.get("request_success"),
            "task_success": request.get("task_success"),
            "attempt_count": _optional_int(request.get("attempt_count")),
            "cache_state": request.get("cache_state"),
            "prompt_tokens": _optional_int(request.get("prompt_tokens")),
            "cache_hit_tokens": _optional_int(request.get("cache_hit_tokens")),
            "cache_miss_tokens": _optional_int(request.get("cache_miss_tokens")),
            "latency_ms": _optional_int(request.get("latency_ms")),
            "request_sha256": request.get("request_sha256"),
            "response_sha256": request.get("response_sha256"),
            "stable_prefix_sha256": request.get("stable_prefix_sha256"),
            "response_model": request.get("response_model"),
            "system_fingerprint": request.get("system_fingerprint"),
        }
        for request in requests
    ]
    return {
        "request_count": _integer(probe.get("request_count")),
        "warmup_requests": _integer(probe.get("warmup_requests")),
        "steady_state_request_count": _integer(probe.get("steady_state_request_count")),
        "cache": dict(probe.get("cache") or {}),
        "steady_state_cache": dict(probe.get("steady_state_cache") or {}),
        "stable_prefix": stable,
        "stable_prefix_estimated_tokens": _optional_int(stable.get("estimated_tokens_min")),
        "stable_prefix_chars": _optional_int(stable.get("chars_min")),
        "stable_prefix_consistent": bool(stable.get("consistent")),
        "attempts_reported": len(attempts),
        "attempts_exactly_one": len(attempts) == len(requests) and all(
            value == 1 for value in attempts
        ),
        "retried_requests": sum(value > 1 for value in attempts),
        "requests_succeeded": requests_succeeded,
        "request_hashes_complete_and_unique": (
            bool(request_hashes)
            and all(request_hashes)
            and len(set(request_hashes)) == len(request_hashes)
        ),
        "request_hashes": request_hashes,
        "latency_metrics_complete": (
            len(requests) == _integer((probe.get("cache") or {}).get("latency_samples"))
            and all(_integer(request.get("latency_ms")) > 0 for request in requests)
        ),
        "requests": request_rows,
        "response_models": sorted(
            {
                str(request["response_model"])
                for request in requests
                if request.get("response_model")
            }
        ),
        "system_fingerprints": sorted(
            {
                str(request["system_fingerprint"])
                for request in requests
                if request.get("system_fingerprint")
            }
        ),
    }


def _real_suite_metrics(suite: Mapping[str, Any]) -> dict[str, Any]:
    cases = [case for case in suite.get("cases", []) if isinstance(case, Mapping)]
    run_metrics = [
        metrics
        for case in cases
        if isinstance((metrics := case.get("metrics")), Mapping)
    ]
    request_count = sum(
        _integer(metrics.get("cache_metric_requests"))
        + _integer(metrics.get("cache_unreported_requests"))
        for metrics in run_metrics
    )
    metric_requests = sum(_integer(metrics.get("cache_metric_requests")) for metrics in run_metrics)
    unreported_requests = sum(
        _integer(metrics.get("cache_unreported_requests")) for metrics in run_metrics
    )
    hit_tokens = sum(
        _integer(metrics.get("cache_observed_hit_tokens")) for metrics in run_metrics
    )
    observed_prompt_tokens = sum(
        _integer(metrics.get("cache_observed_prompt_tokens")) for metrics in run_metrics
    )
    prompt_values = [
        value
        for metrics in run_metrics
        if (value := _optional_int(metrics.get("prompt_tokens"))) is not None
    ]
    observed_prompt_values = [
        value
        for metrics in run_metrics
        if (
            value := _optional_int(metrics.get("cache_observed_prompt_tokens"))
        )
        is not None
    ]
    prompt_metrics_complete = (
        len(run_metrics) == len(cases)
        and len(prompt_values) == len(cases)
        and len(observed_prompt_values) == len(cases)
        and all(value > 0 for value in prompt_values)
        and all(value > 0 for value in observed_prompt_values)
        and prompt_values == observed_prompt_values
    )
    miss_tokens = max(observed_prompt_tokens - hit_tokens, 0)
    if metric_requests == 0:
        coverage_status = "unsupported"
        cache_state = "unsupported"
        hit_rate = None
    else:
        coverage_status = "complete" if unreported_requests == 0 else "partial"
        cache_state = "zero_hit" if hit_tokens == 0 else "nonzero_hit"
        hit_rate = hit_tokens / observed_prompt_tokens if observed_prompt_tokens else 0.0
    latency_values = [
        value
        for metrics in run_metrics
        if (value := _optional_int(metrics.get("latency_ms"))) is not None
    ]
    latency_total = sum(latency_values)
    latency_metrics_complete = (
        len(latency_values) == len(cases)
        and all(value > 0 for value in latency_values)
    )
    cache = {
        "request_count": request_count,
        "successful_requests": request_count,
        "metric_requests": metric_requests,
        "unreported_requests": unreported_requests,
        "coverage_status": coverage_status,
        "cache_state": cache_state,
        "hit_tokens": hit_tokens,
        "miss_tokens": miss_tokens,
        "observed_prompt_tokens": observed_prompt_tokens,
        "weighted_hit_rate": hit_rate,
        "prompt_tokens": sum(_integer(metrics.get("prompt_tokens")) for metrics in run_metrics),
        "latency_samples": request_count if latency_metrics_complete else 0,
        "latency_ms_total": latency_total,
        "latency_ms_mean": latency_total / request_count if request_count else None,
    }
    task_successes = sum(bool(case.get("task_success")) for case in cases)
    passed_runs = sum(bool(case.get("passed")) for case in cases)
    agent_successes = sum(bool(case.get("agent_success")) for case in cases)
    infrastructure_successes = sum(bool(case.get("infrastructure_success")) for case in cases)
    stable_tokens = _metric_values(
        run_metrics,
        "stable_prefix_estimated_tokens",
        "prompt_cache_stable_prefix_estimated_tokens",
    )
    stable_chars = _metric_values(
        run_metrics,
        "stable_prefix_chars",
        "prompt_cache_stable_prefix_chars",
    )
    stable_hashes = {
        str(value)
        for metrics in run_metrics
        for key in (
            "stable_prefix_sha256",
            "stable_prefix_hash",
            "prompt_cache_stable_prefix_sha256",
        )
        if (value := metrics.get(key))
    }
    config = suite.get("configuration") or {}
    configured_tokens = _optional_int(config.get("stable_prefix_estimated_tokens"))
    configured_chars = _optional_int(config.get("stable_prefix_chars"))
    if configured_tokens is not None:
        stable_tokens.append(configured_tokens)
    if configured_chars is not None:
        stable_chars.append(configured_chars)
    configured_hash = config.get("stable_prefix_sha256", config.get("stable_prefix_hash"))
    if configured_hash:
        stable_hashes.add(str(configured_hash))
    cache["task_success_rate"] = task_successes / len(cases) if cases else None
    run_rows = []
    for case in cases:
        metrics = case.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        run_observed = _optional_int(metrics.get("cache_observed_prompt_tokens"))
        run_hit = _optional_int(metrics.get("cache_observed_hit_tokens"))
        run_rows.append(
            {
                "run_id": case.get("run_id"),
                "name": case.get("name"),
                "attempt": _integer(case.get("attempt")),
                "passed": case.get("passed"),
                "task_success": case.get("task_success"),
                "agent_success": case.get("agent_success"),
                "infrastructure_success": case.get("infrastructure_success"),
                "provider_requests": (
                    _integer(metrics.get("cache_metric_requests"))
                    + _integer(metrics.get("cache_unreported_requests"))
                ),
                "prompt_tokens": _optional_int(metrics.get("prompt_tokens")),
                "cache_hit_tokens": run_hit,
                "cache_miss_tokens": (
                    max(run_observed - run_hit, 0)
                    if run_observed is not None and run_hit is not None
                    else None
                ),
                "latency_ms": _optional_int(metrics.get("latency_ms")),
                "provider_request_attempts": _optional_int(
                    metrics.get("provider_request_attempts")
                ),
                "provider_retried_requests": _optional_int(
                    metrics.get("provider_retried_requests")
                ),
                "prompt_layout": metrics.get("prompt_layout"),
                "stable_prefix_sha256": (
                    metrics.get("stable_prefix_sha256")
                    or metrics.get("stable_prefix_hash")
                ),
            }
        )
    return {
        "runs": len(cases),
        "passed_runs": passed_runs,
        "pass_rate": passed_runs / len(cases) if cases else 0.0,
        "task_successes": task_successes,
        "task_success_rate": task_successes / len(cases) if cases else 0.0,
        "cache": cache,
        "stable_prefix": {
            "sha256": next(iter(stable_hashes)) if len(stable_hashes) == 1 else None,
            "consistent": bool(stable_hashes) and len(stable_hashes) == 1,
            "estimated_tokens_min": min(stable_tokens) if stable_tokens else None,
            "estimated_tokens_max": max(stable_tokens) if stable_tokens else None,
            "chars_min": min(stable_chars) if stable_chars else None,
            "chars_max": max(stable_chars) if stable_chars else None,
        },
        "stable_prefix_estimated_tokens": min(stable_tokens) if stable_tokens else None,
        "stable_prefix_consistent": bool(stable_hashes) and len(stable_hashes) == 1,
        "prompt_metrics_complete": prompt_metrics_complete,
        "latency_metrics_complete": latency_metrics_complete,
        "run_rows": run_rows,
        "agent_successes": agent_successes,
        "infrastructure_successes": infrastructure_successes,
        "retried_requests": sum(
            _integer(metrics.get("provider_retried_requests"))
            for metrics in run_metrics
        ),
        "provider_request_attempts": sum(
            _integer(metrics.get("provider_request_attempts"))
            for metrics in run_metrics
        ),
        "response_models": sorted(
            {
                str(value)
                for metrics in run_metrics
                for value in metrics.get("provider_response_models", [])
                if value
            }
        ),
        "system_fingerprints": sorted(
            {
                str(value)
                for metrics in run_metrics
                for value in metrics.get("provider_system_fingerprints", [])
                if value
            }
        ),
    }


def _pair_configuration_errors(
    p0: Mapping[str, Any],
    p1: Mapping[str, Any],
    *,
    label: str,
) -> list[str]:
    ignored = {
        "prompt_cache_variant",
        "cache_variant",
        "variant",
        "prompt_layout",
        "stable_prefix_sha256",
        "stable_prefix_hash",
        "stable_prefix_chars",
        "stable_prefix_estimated_tokens",
        "stable_prefix_message_count",
        "stable_prefix_profile",
        "prefix_profile",
    }
    p0_config = dict(p0.get("configuration") or {})
    p1_config = dict(p1.get("configuration") or {})
    errors = []
    for key in sorted((set(p0_config) | set(p1_config)) - ignored):
        if p0_config.get(key) != p1_config.get(key):
            errors.append(f"{label} configuration differs for {key}")
    return errors


def _required_configuration_errors(
    payload: Mapping[str, Any],
    *,
    kind: str,
) -> list[str]:
    config = payload.get("configuration") or {}
    required = {
        "base_url",
        "model",
        "temperature",
        "json_mode",
        "git_commit",
        "system_prefix_sha256",
        "cache_sequence_id",
        "stream",
        "include_usage",
        "provider_max_retries",
        "provider_timeout_sec",
        "recent_turns",
        "max_prompt_chars",
        "cache_scope_sha256",
        "execution_order",
        "release_gate",
        "worktree_dirty",
        "milestone",
        "compaction_strategy",
        "prompt_layout",
        "feedback_memory_mode",
    }
    if "probe" in kind:
        required.add("dynamic_sequence_sha256")
    if "suite" in kind:
        required.update(
            {
                "docker_image",
                "sandbox_mode",
                "execute_local",
                "case_contexts",
            }
        )
    return [
        f"{kind} configuration is missing {key}"
        for key in sorted(required)
        if key not in config or config.get(key) is None or config.get(key) == ""
    ]


def _cross_workload_errors(*payloads: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "base_url",
        "model",
        "temperature",
        "json_mode",
        "git_commit",
        "system_prefix_sha256",
        "compaction_strategy",
        "cache_sequence_id",
        "stream",
        "include_usage",
        "provider_max_retries",
        "provider_timeout_sec",
        "recent_turns",
        "max_prompt_chars",
        "cache_scope_sha256",
        "execution_order",
        "release_gate",
        "worktree_dirty",
        "milestone",
        "feedback_memory_mode",
    ):
        values = {
            _json_key(config[key])
            for payload in payloads
            if key in (config := (payload.get("configuration") or {}))
        }
        if len(values) > 1:
            errors.append(f"fixed/real configuration differs for {key}")
    return errors


def _global_comparability_errors(rounds: Sequence[CacheABRound]) -> list[str]:
    payloads = [payload for round_ in rounds for payload in round_]
    errors: list[str] = []
    for key in (
        "base_url",
        "model",
        "temperature",
        "json_mode",
        "git_commit",
        "system_prefix_sha256",
        "compaction_strategy",
        "stream",
        "include_usage",
        "provider_max_retries",
        "provider_timeout_sec",
        "recent_turns",
        "max_prompt_chars",
        "cache_scope_sha256",
        "milestone",
        "feedback_memory_mode",
        "docker_image",
        "sandbox_mode",
        "execute_local",
        "case_contexts",
    ):
        values = {
            _json_key(config[key])
            for payload in payloads
            if key in (config := (payload.get("configuration") or {}))
        }
        if len(values) > 1:
            errors.append(f"rounds differ for {key}")
    return errors


def _case_matrix(suite: Mapping[str, Any]) -> list[tuple[str, int]]:
    return [
        (str(case.get("name")), _integer(case.get("attempt")))
        for case in suite.get("cases", [])
        if isinstance(case, Mapping)
    ]


def _request_indices(probe: Mapping[str, Any]) -> list[int]:
    return [
        _integer(request.get("request_index"))
        for request in probe.get("requests", [])
        if isinstance(request, Mapping)
    ]


def _fixed_prompt_not_larger(
    p0_probe: Mapping[str, Any],
    p1_probe: Mapping[str, Any],
) -> bool:
    def prompt_matrix(probe: Mapping[str, Any]) -> list[tuple[int, int | None]]:
        return [
            (
                _integer(request.get("request_index")),
                _optional_int(request.get("prompt_tokens")),
            )
            for request in probe.get("requests", [])
            if isinstance(request, Mapping)
        ]

    p0 = prompt_matrix(p0_probe)
    p1 = prompt_matrix(p1_probe)
    return (
        bool(p0)
        and len(p0) == len(p1)
        and [index for index, _tokens in p0] == [index for index, _tokens in p1]
        and all(
            p0_tokens is not None
            and p1_tokens is not None
            and p1_tokens <= p0_tokens
            for (_p0_index, p0_tokens), (_p1_index, p1_tokens) in zip(p0, p1, strict=True)
        )
    )


def _real_suite_all_passed(
    suite: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> bool:
    runs = _integer(metrics.get("runs"))
    return (
        runs > 0
        and suite.get("passed") is True
        and suite.get("result") == "PASS"
        and _integer(metrics.get("passed_runs")) == runs
        and _integer(metrics.get("task_successes")) == runs
        and _integer(metrics.get("agent_successes")) == runs
        and _integer(metrics.get("infrastructure_successes")) == runs
    )


def _execution_order_verified(
    execution_order: str,
    *,
    p0_probe: Mapping[str, Any],
    p1_probe: Mapping[str, Any],
    p0_suite: Mapping[str, Any],
    p1_suite: Mapping[str, Any],
) -> bool:
    p0_probe_time = _timestamp(p0_probe.get("created_at"))
    p1_probe_time = _timestamp(p1_probe.get("created_at"))
    p0_probe_completed = _timestamp(p0_probe.get("completed_at"))
    p1_probe_completed = _timestamp(p1_probe.get("completed_at"))
    p0_suite_time = _timestamp(p0_suite.get("created_at"))
    p1_suite_time = _timestamp(p1_suite.get("created_at"))
    p0_suite_completed = _timestamp(p0_suite.get("completed_at"))
    p1_suite_completed = _timestamp(p1_suite.get("completed_at"))
    timestamps = (
        p0_probe_time,
        p1_probe_time,
        p0_probe_completed,
        p1_probe_completed,
        p0_suite_time,
        p1_suite_time,
        p0_suite_completed,
        p1_suite_completed,
    )
    if any(timestamp is None for timestamp in timestamps):
        return False
    assert p0_probe_time is not None
    assert p1_probe_time is not None
    assert p0_probe_completed is not None
    assert p1_probe_completed is not None
    assert p0_suite_time is not None
    assert p1_suite_time is not None
    assert p0_suite_completed is not None
    assert p1_suite_completed is not None
    probes_before_real = max(p0_probe_completed, p1_probe_completed) <= min(
        p0_suite_time,
        p1_suite_time,
    )
    if execution_order == "p0-first":
        return (
            probes_before_real
            and p0_probe_completed <= p1_probe_time
            and p0_suite_completed <= p1_suite_time
        )
    if execution_order == "p1-first":
        return (
            probes_before_real
            and p1_probe_completed <= p0_probe_time
            and p1_suite_completed <= p0_suite_time
        )
    return False


def _runtime_identity_errors(*metrics: Mapping[str, Any]) -> list[str]:
    models = {
        str(value)
        for item in metrics
        for value in item.get("response_models", [])
        if value
    }
    fingerprints = {
        str(value)
        for item in metrics
        for value in item.get("system_fingerprints", [])
        if value
    }
    errors = []
    if len(models) > 1:
        errors.append("provider response model differs within round")
    if len(fingerprints) > 1:
        errors.append("provider system fingerprint differs within round")
    return errors


def _global_runtime_identity_errors(
    rounds: Sequence[Mapping[str, Any]],
) -> list[str]:
    fixed_and_real = [
        metrics
        for round_ in rounds
        for variant in ("p0", "p1")
        for metrics in (
            round_[variant]["fixed"],
            round_[variant]["real"],
        )
    ]
    errors = _runtime_identity_errors(*fixed_and_real)
    return [error.replace("within round", "across rounds") for error in errors]


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _stable_prefix_not_lower(
    p0_fixed: Mapping[str, Any],
    p1_fixed: Mapping[str, Any],
) -> bool:
    p0 = _optional_int(p0_fixed.get("stable_prefix_estimated_tokens"))
    p1 = _optional_int(p1_fixed.get("stable_prefix_estimated_tokens"))
    return (
        p0 is not None
        and p1 is not None
        and bool((p0_fixed.get("stable_prefix") or {}).get("sha256"))
        and bool((p1_fixed.get("stable_prefix") or {}).get("sha256"))
        and bool(p0_fixed.get("stable_prefix_consistent"))
        and bool(p1_fixed.get("stable_prefix_consistent"))
        and p1 >= p0
    )


def _cache_improved(p0: Mapping[str, Any], p1: Mapping[str, Any]) -> bool:
    p0_rate = _optional_float(p0.get("weighted_hit_rate"))
    p1_rate = _optional_float(p1.get("weighted_hit_rate"))
    if p0_rate is None or p1_rate is None:
        return False
    p0_hit = _integer(p0.get("hit_tokens"))
    p1_hit = _integer(p1.get("hit_tokens"))
    p0_miss = _integer(p0.get("miss_tokens"))
    p1_miss = _integer(p1.get("miss_tokens"))
    return p1_rate > p0_rate or (
        p1_rate >= p0_rate
        and p1_hit > p0_hit
        and p1_miss <= p0_miss
    )


def _cache_not_lower(p0: Mapping[str, Any], p1: Mapping[str, Any]) -> bool:
    p0_rate = _optional_float(p0.get("weighted_hit_rate"))
    p1_rate = _optional_float(p1.get("weighted_hit_rate"))
    if p0_rate is None or p1_rate is None:
        return False
    return p1_rate >= p0_rate


def _combine_cache(*items: Mapping[str, Any]) -> dict[str, Any]:
    requests = sum(_integer(item.get("request_count")) for item in items)
    metric_requests = sum(_integer(item.get("metric_requests")) for item in items)
    unreported = sum(_integer(item.get("unreported_requests")) for item in items)
    hit = sum(_integer(item.get("hit_tokens")) for item in items)
    miss = sum(_integer(item.get("miss_tokens")) for item in items)
    observed = hit + miss
    return {
        "request_count": requests,
        "metric_requests": metric_requests,
        "unreported_requests": unreported,
        "coverage_status": (
            "unsupported"
            if metric_requests == 0
            else "complete"
            if unreported == 0
            else "partial"
        ),
        "cache_state": (
            "unsupported" if metric_requests == 0 else "zero_hit" if hit == 0 else "nonzero_hit"
        ),
        "hit_tokens": hit,
        "miss_tokens": miss,
        "observed_prompt_tokens": observed,
        "weighted_hit_rate": hit / observed if observed else (0.0 if metric_requests else None),
        "prompt_tokens": sum(_integer(item.get("prompt_tokens")) for item in items),
        "latency_ms_total": sum(_integer(item.get("latency_ms_total")) for item in items),
    }


def _rate_delta(p0: Mapping[str, Any], p1: Mapping[str, Any]) -> float | None:
    p0_rate = _optional_float(p0.get("weighted_hit_rate"))
    p1_rate = _optional_float(p1.get("weighted_hit_rate"))
    return None if p0_rate is None or p1_rate is None else p1_rate - p0_rate


def _validate_variant(payload: Mapping[str, Any], expected: str, *, kind: str) -> None:
    config = payload.get("configuration") or {}
    actual = str(
        payload.get("variant")
        or config.get("prompt_cache_variant")
        or config.get("cache_variant")
        or ""
    ).lower()
    if actual != expected:
        raise ValueError(f"expected {expected} {kind}, got prompt cache variant={actual!r}")
    expected_layout = "append" if expected == "p1" else "rebuild"
    actual_layout = str(config.get("prompt_layout") or "").lower()
    if actual_layout != expected_layout:
        raise ValueError(
            f"expected {expected} {kind} to use prompt_layout={expected_layout}, "
            f"got {actual_layout!r}"
        )


def _metric_values(
    metrics: Sequence[Mapping[str, Any]],
    *keys: str,
) -> list[int]:
    values: list[int] = []
    for item in metrics:
        for key in keys:
            value = _optional_int(item.get(key))
            if value is not None:
                values.append(value)
                break
    return values


def _workload_row(
    workload: str,
    variant: str,
    cache: Mapping[str, Any],
    workload_metrics: Mapping[str, Any],
) -> str:
    stable_tokens = workload_metrics.get("stable_prefix_estimated_tokens")
    task_rate = (
        workload_metrics.get("pass_rate")
        if workload == "real"
        else cache.get("task_success_rate")
    )
    return (
        f"| {workload} | {variant} | {cache.get('request_count', 0)} | "
        f"{cache.get('metric_requests', 0)} | {cache.get('unreported_requests', 0)} | "
        f"{cache.get('hit_tokens', 0)} | {cache.get('miss_tokens', 0)} | "
        f"{_format_rate(cache.get('weighted_hit_rate'))} | {cache.get('prompt_tokens', 0)} | "
        f"{cache.get('latency_ms_total', 0)}/{_display_number(cache.get('latency_ms_mean'))} | "
        f"{_format_rate(_optional_float(task_rate))} | "
        f"{'n/a' if stable_tokens is None else stable_tokens} |"
    )


def _fixed_request_row(variant: str, request: Mapping[str, Any]) -> str:
    hit = _optional_int(request.get("cache_hit_tokens"))
    miss = _optional_int(request.get("cache_miss_tokens"))
    rate = (
        hit / (hit + miss)
        if hit is not None and miss is not None and hit + miss
        else 0.0
        if hit is not None and miss is not None
        else None
    )
    request_hash = str(request.get("request_sha256") or "missing")[:12]
    return (
        f"| {variant} | {request.get('request_index')} | "
        f"{'PASS' if request.get('request_success') is True else 'FAIL'} | "
        f"{'PASS' if request.get('task_success') is True else 'FAIL'} | "
        f"{_display_number(request.get('attempt_count'))} | "
        f"{request.get('cache_state') or 'unknown'} | "
        f"{_display_number(request.get('prompt_tokens'))} | "
        f"{_display_number(hit)} | {_display_number(miss)} | "
        f"{_format_rate(rate)} | {_display_number(request.get('latency_ms'))} | "
        f"`{request_hash}` |"
    )


def _real_run_row(variant: str, run: Mapping[str, Any]) -> str:
    return (
        f"| {variant} | {run.get('attempt')} | `{run.get('run_id')}` | "
        f"{'PASS' if run.get('passed') is True else 'FAIL'} | "
        f"{'PASS' if run.get('task_success') is True else 'FAIL'} | "
        f"{'PASS' if run.get('agent_success') is True else 'FAIL'} | "
        f"{'PASS' if run.get('infrastructure_success') is True else 'FAIL'} | "
        f"{_display_number(run.get('provider_requests'))} | "
        f"{_display_number(run.get('prompt_tokens'))} | "
        f"{_display_number(run.get('cache_hit_tokens'))} | "
        f"{_display_number(run.get('cache_miss_tokens'))} | "
        f"{_display_number(run.get('latency_ms'))} | "
        f"{_display_number(run.get('provider_request_attempts'))} | "
        f"{_display_number(run.get('provider_retried_requests'))} | "
        f"{run.get('prompt_layout') or 'unknown'} |"
    )


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _integer(value: Any) -> int:
    return _optional_int(value) or 0


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_rate(value: Any) -> str:
    parsed = _optional_float(value)
    return "unsupported" if parsed is None else f"{parsed:.2%}"


def _format_delta(value: Any) -> str:
    parsed = _optional_float(value)
    return "unsupported" if parsed is None else f"{parsed:+.2%}"


def _display_number(value: Any) -> str:
    parsed = _optional_float(value)
    return "n/a" if parsed is None else f"{parsed:.1f}"
