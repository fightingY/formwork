import hashlib
import json
from pathlib import Path

import pytest

from minicc.core.ledger import write_artifact_index
from minicc.evals.memory_ab import MemoryABResult, write_memory_ab_report
from minicc.evals.memory_acceptance import (
    REQUIRED_MEMORY_CASES,
    build_memory_acceptance_report,
    load_memory_suite_report,
    write_memory_acceptance_report,
)
from minicc.evals.runner import EvalCaseResult


MODEL = "provider/model"


def test_formal_memory_suite_loader_verifies_runs_and_acceptance_archive(tmp_path) -> None:
    suites = [
        _formal_suite(tmp_path, case_name, index)
        for index, case_name in enumerate(REQUIRED_MEMORY_CASES, start=1)
    ]

    report = build_memory_acceptance_report(suites)

    assert report["passed"] is True
    assert report["criteria"]["follow_up_key_fact_accuracy_m0"] == 1.0
    assert report["criteria"]["follow_up_key_fact_accuracy_m1"] == 1.0
    assert report["aggregate"]["run_count"] == 27
    assert report["aggregate"]["m0_repeated_source_file_reads"] == 9
    assert report["aggregate"]["m1_repeated_source_file_reads"] == 0
    bundle = write_memory_acceptance_report(report, tmp_path / "acceptance" / "stable-v2.2")
    assert {path.name for path in bundle.json_path.parent.iterdir()} == {
        "report.json",
        "report.md",
        "evidence.json",
        "manifest.json",
    }
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_commit"] == "abc123"
    assert len(manifest["input_evidence"]) == 3


def test_memory_suite_loader_rejects_trace_tampering(tmp_path) -> None:
    suite = _formal_suite(tmp_path, next(iter(REQUIRED_MEMORY_CASES)), 1)
    run_id = suite["attempts"][0]["m1"]["run_id"]
    trace = tmp_path / ".minicc" / "runs" / run_id / "trace.jsonl"
    trace.write_text(trace.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="integrity"):
        load_memory_suite_report(Path(suite["_evidence_source_path"]), verify_manifest=True)


def test_memory_acceptance_rejects_configuration_drift(tmp_path) -> None:
    suites = [
        _formal_suite(tmp_path, case_name, index)
        for index, case_name in enumerate(REQUIRED_MEMORY_CASES, start=1)
    ]
    suites[2]["configuration"]["model"] = "different/model"

    report = build_memory_acceptance_report(suites)

    assert report["passed"] is False
    assert report["criteria"]["locked_configuration_consistent"] is False


def _formal_suite(tmp_path: Path, case_name: str, index: int) -> dict:
    suite_id = f"suite-formal-{index}"
    expected_case, expected_fixture = REQUIRED_MEMORY_CASES[case_name]
    expected_path = f"docs/CONTRACT_{index}.md"
    attempts: list[dict] = []
    results: list[EvalCaseResult] = []
    for attempt in range(1, 4):
        source_id = f"{case_name}-source-{attempt}"
        source_commands = [f"cat {expected_path}"]
        _run_evidence(
            tmp_path,
            suite_id,
            source_id,
            commands=source_commands,
            memory_event={"event": "memory_reference_captured", "reference": {"path": expected_path}},
            working_memory=True,
        )
        source_result = _case_result(tmp_path, suite_id, source_id, f"{case_name}_source", attempt)
        results.append(source_result)
        variants: dict[str, dict] = {}
        for variant in ("m0", "m1"):
            run_id = f"{case_name}-{variant}-{attempt}"
            commands = [f"cat {expected_path}"] if variant == "m0" else ["cat src/current.py"]
            memory_event = (
                {
                    "event": "working_memory_injected",
                    "source_run_id": source_id,
                    "references": [{"path": expected_path}],
                }
                if variant == "m1"
                else None
            )
            _run_evidence(
                tmp_path,
                suite_id,
                run_id,
                commands=commands,
                memory_event=memory_event,
            )
            results.append(
                _case_result(tmp_path, suite_id, run_id, f"{case_name}_follow_up_{variant}", attempt)
            )
            variants[variant] = {
                "variant": variant,
                "run_id": run_id,
                "run_dir": str(tmp_path / ".minicc" / "runs" / run_id),
                "passed": True,
                "key_facts_correct": True,
                "prompt_tokens": 100 if variant == "m0" else 80,
                "repeated_source_file_reads": 1 if variant == "m0" else 0,
                "commands": commands,
                "memory_injection_events": 0 if variant == "m0" else 1,
                "memory_items_injected": 0 if variant == "m0" else 1,
                "old_run_memory_leaks": 0,
                "irrelevant_memory_injections": 0,
                "integrity_invalid_memory_adoptions": 0,
            }
        attempts.append(
            {
                "attempt": attempt,
                "execution_order": ["m0", "m1"] if attempt % 2 else ["m1", "m0"],
                "source": {
                    "run_id": source_id,
                    "run_dir": str(tmp_path / ".minicc" / "runs" / source_id),
                    "passed": True,
                    "prompt_tokens": 50,
                    "captured_references": [{"path": expected_path}],
                    "working_memory_path": str(
                        tmp_path / ".minicc" / "runs" / source_id / "working_memory.json"
                    ),
                    "working_memory_sha256": "a" * 64,
                    "commands": source_commands,
                },
                **variants,
                "paired_read_reduction": 1,
                "paired_read_decreased": True,
            }
        )
    configuration = {
        "base_url": "https://provider.test/v1",
        "model": MODEL,
        "temperature": 0.0,
        "provider_timeout_sec": 300.0,
        "provider_max_retries": 2,
        "sandbox_mode": "locked",
        "docker_image": "python@sha256:" + "a" * 64,
        "git_commit": "abc123",
        "worktree_dirty": False,
        "release_gate": True,
        "case_name": case_name,
        "case_authority_profiles": {
            f"{case_name}_source": {
                "source_path": expected_case,
                "fixture_source_path": expected_fixture,
                "case_definition_sha256": "b" * 64,
                "fixture_content_sha256": "c" * 64,
            }
        },
        "git_preflight_verified": True,
        "git_postflight_verified": True,
        "feedback_memory_mode": "disabled",
        "working_memory_mode": "explicit_source_run",
        "prompt_layout": "append",
        "compaction_strategy": "deterministic",
        "expected_memory_paths": [expected_path],
    }
    result = MemoryABResult(
        suite_id=suite_id,
        milestone="v2.2-acceptance",
        stage="formal_acceptance",
        created_at="2026-08-02T00:00:00+00:00",
        completed_at="2026-08-02T00:01:00+00:00",
        repeat=3,
        passed=True,
        configuration=configuration,
        attempts=attempts,
        case_results=results,
    )
    bundle = write_memory_ab_report(result, tmp_path / ".minicc" / "suites")
    return load_memory_suite_report(bundle.report_json_path, verify_manifest=True)


def _run_evidence(
    tmp_path: Path,
    suite_id: str,
    run_id: str,
    *,
    commands: list[str],
    memory_event: dict | None,
    working_memory: bool = False,
) -> None:
    run_dir = tmp_path / ".minicc" / "runs" / run_id
    run_dir.mkdir(parents=True)
    events = [
        {"event": "action_parsed", "action": {"type": "bash", "command": command}}
        for command in commands
    ]
    if memory_event:
        events.append(memory_event)
    files = {
        "state": run_dir / "state.json",
        "trace": run_dir / "trace.jsonl",
        "metrics": run_dir / "metrics.json",
        "workspace_manifest": run_dir / "workspace_manifest.json",
        "diff": run_dir / "artifacts" / "diff.patch",
        "run_report": run_dir / "eval_result.json",
    }
    files["diff"].parent.mkdir()
    files["state"].write_text(json.dumps({"run_id": run_id, "status": "completed"}), encoding="utf-8")
    files["trace"].write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    files["metrics"].write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "provider_errors": 0,
                "provider_retried_requests": 0,
                "protocol_errors": 0,
                "provider_response_models": [MODEL],
            }
        ),
        encoding="utf-8",
    )
    files["workspace_manifest"].write_text("{}", encoding="utf-8")
    files["diff"].write_bytes(b"")
    files["run_report"].write_text(
        json.dumps(
            {
                "run_id": run_id,
                "suite_id": suite_id,
                "passed": True,
                "formal_metric_eligible": True,
            }
        ),
        encoding="utf-8",
    )
    if working_memory:
        files["working_memory"] = run_dir / "working_memory.json"
        files["working_memory"].write_text('{"grounded":true}', encoding="utf-8")
    write_artifact_index(
        tmp_path / ".minicc" / "artifacts",
        run_id=run_id,
        run_dir=run_dir,
        evidence={name: str(path) for name, path in files.items()},
        hash_artifacts=True,
    )


def _case_result(
    tmp_path: Path,
    suite_id: str,
    run_id: str,
    name: str,
    attempt: int,
) -> EvalCaseResult:
    return EvalCaseResult(
        name=name,
        capability="working_memory",
        passed=True,
        run_status="completed",
        run_id=run_id,
        run_dir=str(tmp_path / ".minicc" / "runs" / run_id),
        assertions=[],
        metrics={},
        attempt=attempt,
        task_success=True,
        agent_success=True,
        infrastructure_success=True,
        policy_outcome="clear",
        suite_id=suite_id,
        milestone="v2.2-acceptance",
        stage="formal_acceptance",
    )
