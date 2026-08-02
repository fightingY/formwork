import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from minicc import __version__, cli
from minicc.config import BudgetSettings, ContextSettings, PolicySettings, ProviderSettings, SandboxSettings, Settings
from minicc.core.protocol import BashAction
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.state import Observation, RunState, save_run_state


class FakeProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            text=self.responses.pop(0),
            raw={},
            usage=ModelUsage(prompt_tokens=5, completion_tokens=2),
            latency_ms=3,
        )


class FakeExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def run(self, action: BashAction, state: RunState) -> Observation:
        return Observation(kind="command_result", exit_code=0, stdout_preview="ok")


class FakeLoop:
    def __init__(self, result_state: RunState) -> None:
        self.result_state = result_state

    def run(self, state: RunState):
        state.status = "completed"
        state.final_answer = "done"
        return type("LoopResult", (), {"state": state})()


def test_run_command_fake_provider_writes_complete_evidence_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        provider=ProviderSettings(base_url="https://example.test/v1", api_key="key", model="model"),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(max_turns=2),
        context=ContextSettings(),
        policy=PolicySettings(),
    )
    provider = FakeProvider(
        [
            '{"type":"bash","command":"echo ok","purpose":"smoke test"}',
            '{"type":"final","answer":"done"}',
        ]
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: provider)
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    exit_code = cli.run_command(
        argparse.Namespace(
            goal="fake provider smoke test",
            max_turns=None,
            execute_local=True,
            no_workspace_copy=False,
            docker_image=None,
            stream=None,
        )
    )

    run_dirs = [path for path in (tmp_path / ".minicc" / "runs").iterdir() if path.is_dir()]
    assert exit_code == 0
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for relative_path in [
        "state.json",
        "trace.jsonl",
        "metrics.json",
        "workspace_manifest.json",
        "artifacts/diff.patch",
        "run_report.json",
        "run_report.md",
    ]:
        assert (run_dir / relative_path).exists()
    assert (tmp_path / ".minicc" / "artifacts" / run_dir.name / "manifest.json").exists()


def test_eval_command_writes_one_suite_run_artifact_index_and_version_pointer(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    case_dir = tmp_path / "eval_cases" / "demo"
    fixture = case_dir / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "README.md").write_text("ready\n", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        "name: demo\n"
        "prompt: Finish.\n"
        "assertions:\n"
        "  - type: trace_action_shape\n"
        "    actions:\n"
        "      - command: echo ok\n"
        "        expect_exit_code: 0\n",
        encoding="utf-8",
    )
    settings = Settings(
        provider=ProviderSettings(base_url="https://example.test/v1", api_key="key", model="model"),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(max_turns=2),
        context=ContextSettings(),
        policy=PolicySettings(),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_build_provider_or_print_error",
        lambda loaded: FakeProvider(
            [
                '{"type":"bash","command":"echo ok"}',
                '{"type":"final","answer":"done"}',
            ]
        ),
    )
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    exit_code = cli.eval_command(
        argparse.Namespace(
            path=tmp_path / "eval_cases",
            milestone="stable-v2.0.2",
            execute_local=True,
            repeat=1,
            output_dir=None,
            case_names=["demo"],
            release_gate=False,
            cache_variant="p1",
            cache_sequence_id="round-test",
            execution_order="p0-first",
        )
    )

    suites = list((tmp_path / ".minicc" / "suites").iterdir())
    runs = list((tmp_path / ".minicc" / "runs").iterdir())
    assert exit_code == 0
    assert len(suites) == 1
    assert len(runs) == 1
    assert {path.name for path in suites[0].iterdir()} == {
        "manifest.json",
        "report.json",
        "report.md",
        "report.csv",
    }
    assert (tmp_path / ".minicc" / "artifacts" / runs[0].name / "manifest.json").exists()
    version = json.loads(
        (tmp_path / ".minicc" / "versions" / "stable-v2.0.2" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert version["entry_count"] == 1
    assert version["entries"][0]["suite_id"] == suites[0].name
    assert version["entries"][0]["evidence_valid"] is True
    suite_report = json.loads((suites[0] / "report.json").read_text(encoding="utf-8"))
    assert cli.load_cache_suite_report(
        suites[0] / "report.json",
        verify_manifest=True,
    )["suite_id"] == suites[0].name
    assert suite_report["configuration"]["cache_variant"] == "p1"
    assert suite_report["configuration"]["cache_sequence_id"] == "round-test"
    assert suite_report["configuration"]["execution_order"] == "p0-first"
    assert suite_report["configuration"]["prompt_layout"] == "append"
    assert len(
        suite_report["configuration"]["case_authority_bundle_sha256"]
    ) == 64
    case_record = suite_report["cases"][0]
    assert case_record["case_source_path"] == "eval_cases/demo/case.yaml"
    assert case_record["fixture_source_path"] == "eval_cases/demo/fixture"
    assert len(case_record["request_rows"]) == 2
    assert case_record["trace_assertion_events"][0]["action"]["command"] == (
        "echo ok"
    )
    workspace_manifest = json.loads(
        Path(case_record["evidence"]["workspace_manifest"]).read_text(
            encoding="utf-8"
        )
    )
    assert (
        workspace_manifest["included"]["content_digest_sha256"]
        == case_record["fixture_content_sha256"]
    )
    assert suite_report["created_at"]
    artifact_index_path = (
        tmp_path / ".minicc" / "artifacts" / runs[0].name / "manifest.json"
    )
    artifact_index = json.loads(artifact_index_path.read_text(encoding="utf-8"))
    assert {
        "state",
        "trace",
        "metrics",
        "workspace_manifest",
        "diff",
        "run_report",
    }.issubset(artifact_index["artifacts"])
    assert len(artifact_index["artifacts"]["metrics"]["sha256"]) == 64

    report_path = suites[0] / "report.json"
    manifest_path = suites[0] / "manifest.json"
    original_report = report_path.read_bytes()
    original_manifest = manifest_path.read_bytes()
    trace_path = Path(case_record["evidence"]["trace"])
    run_report_path = Path(case_record["evidence"]["run_report"])
    original_trace = trace_path.read_bytes()
    original_run_report = run_report_path.read_bytes()
    original_index = artifact_index_path.read_bytes()
    changed_events = [
        json.loads(line)
        for line in original_trace.decode("utf-8").splitlines()
        if line.strip()
    ]
    model_response = next(
        event for event in changed_events if event.get("event") == "model_response"
    )
    model_response["usage"]["cache_hit_tokens"] = 999
    changed_trace = (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in changed_events
        )
        + "\n"
    ).encode("utf-8")
    trace_path.write_bytes(changed_trace)
    changed_index = json.loads(original_index)
    changed_index["artifacts"]["trace"].update(
        {
            "bytes": len(changed_trace),
            "sha256": hashlib.sha256(changed_trace).hexdigest(),
        }
    )
    artifact_index_path.write_text(
        json.dumps(changed_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="request rows"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    trace_path.write_bytes(original_trace)
    artifact_index_path.write_bytes(original_index)

    changed_events = [
        json.loads(line)
        for line in original_trace.decode("utf-8").splitlines()
        if line.strip()
    ]
    bash_event = next(
        event
        for event in changed_events
        if event.get("event") == "action_parsed"
        and (event.get("action") or {}).get("type") == "bash"
    )
    bash_event["action"]["command"] = "echo tampered"
    changed_trace = (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in changed_events
        )
        + "\n"
    ).encode("utf-8")
    changed_run_report = json.loads(original_run_report)
    changed_run_report["trace_assertion_events"][0]["action"]["command"] = (
        "echo tampered"
    )
    changed_run_report_bytes = (
        json.dumps(changed_run_report, ensure_ascii=False, indent=2)
    ).encode("utf-8")
    forged_report = json.loads(original_report)
    forged_report["cases"][0]["trace_assertion_events"][0]["action"][
        "command"
    ] = "echo tampered"
    forged_report_bytes = (
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    trace_path.write_bytes(changed_trace)
    run_report_path.write_bytes(changed_run_report_bytes)
    changed_index = json.loads(original_index)
    changed_index["artifacts"]["trace"].update(
        {
            "bytes": len(changed_trace),
            "sha256": hashlib.sha256(changed_trace).hexdigest(),
        }
    )
    changed_index["artifacts"]["run_report"].update(
        {
            "bytes": len(changed_run_report_bytes),
            "sha256": hashlib.sha256(changed_run_report_bytes).hexdigest(),
        }
    )
    artifact_index_path.write_text(
        json.dumps(changed_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_bytes(forged_report_bytes)
    forged_manifest = json.loads(original_manifest)
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="action shape"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    trace_path.write_bytes(original_trace)
    run_report_path.write_bytes(original_run_report)
    artifact_index_path.write_bytes(original_index)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    forged_report = json.loads(original_report)
    forged_report["cases"][0]["fixture_content_sha256"] = "0" * 64
    report_path.write_text(
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    forged_manifest = json.loads(original_manifest)
    forged_report_bytes = report_path.read_bytes()
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="authority profile"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    forged_report = json.loads(original_report)
    forged_report["cases"][0]["assertions"] = [
        {
            "type": "trace_action_shape",
            "passed": True,
            "message": "forged assertion result",
            "spec_sha256": "0" * 64,
        }
    ]
    report_path.write_text(
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    forged_manifest = json.loads(original_manifest)
    forged_report_bytes = report_path.read_bytes()
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="action shape"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    forged_report = json.loads(original_report)
    forged_report["cases"][0]["metrics"]["prompt_tokens"] += 1
    report_path.write_text(
        json.dumps(forged_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    forged_manifest = json.loads(original_manifest)
    forged_report_bytes = report_path.read_bytes()
    forged_manifest["artifacts"]["report_json"].update(
        {
            "bytes": len(forged_report_bytes),
            "sha256": hashlib.sha256(forged_report_bytes).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(forged_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run report does not match"):
        cli.load_cache_suite_report(report_path, verify_manifest=True)
    report_path.write_bytes(original_report)
    manifest_path.write_bytes(original_manifest)

    (runs[0] / "metrics.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        cli.load_cache_suite_report(
            suites[0] / "report.json",
            verify_manifest=True,
        )


def test_eval_parser_accepts_cache_variant() -> None:
    args = cli.build_parser().parse_args(["eval", "--cache-variant", "p2"])

    assert args.cache_variant == "p2"


def test_cache_utilization_parser_collects_exact_round_inputs(tmp_path) -> None:
    args = cli.build_parser().parse_args(
        [
            "cache-utilization-report",
            "--p1-probe",
            str(tmp_path / "p1-r1.json"),
            "--p2-probe",
            str(tmp_path / "p2-r1.json"),
            "--p1-eval",
            str(tmp_path / "p1-e1.json"),
            "--p2-eval",
            str(tmp_path / "p2-e1.json"),
            "--output-dir",
            str(tmp_path / "acceptance"),
        ]
    )

    assert args.p1_probe == [tmp_path / "p1-r1.json"]
    assert args.p2_probe == [tmp_path / "p2-r1.json"]


def test_cache_experiment_loop_disables_mutable_feedback_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        provider=ProviderSettings(
            base_url="https://example.test/v1",
            api_key="key",
            model="model",
        ),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(),
        context=ContextSettings(prompt_layout="append"),
        policy=PolicySettings(),
    )
    state = RunState.start(
        "cache experiment",
        prompt_namespace="cache-experiment/round-1",
    )

    loop = cli._build_loop(
        FakeProvider(['{"type":"final","answer":"done"}']),
        FakeExecutor(),
        settings=settings,
        state=state,
    )

    assert loop.context_builder.feedback_memory is None


def test_cache_probe_command_writes_canonical_probe_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        provider=ProviderSettings(
            base_url="https://example.test/v1",
            api_key="key",
            model="model",
            max_completion_tokens=128,
        ),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(),
        context=ContextSettings(),
        policy=PolicySettings(),
    )
    provider = FakeProvider(['{"type":"bash","command":"true"}'] * 5)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: provider)
    monkeypatch.setattr(cli, "_git_evidence", lambda cwd: ("abc123", False))

    exit_code = cli.cache_probe_command(
        argparse.Namespace(
            cache_variant="p1",
            repeat=5,
            milestone="v2.1.1-test",
            execution_order="p0-first",
            cache_sequence_id="round-1",
            release_gate=True,
        )
    )

    probe_dirs = list((tmp_path / ".minicc" / "cache-probes").iterdir())
    assert exit_code == 0
    assert len(probe_dirs) == 1
    report = json.loads((probe_dirs[0] / "report.json").read_text(encoding="utf-8"))
    assert report["stage"] == "formal_acceptance"
    assert report["configuration"]["cache_variant"] == "p1"
    assert report["configuration"]["cache_sequence_id"] == "round-1"
    assert report["configuration"]["prompt_layout"] == "append"
    assert report["configuration"]["dynamic_sequence_sha256"]
    assert report["stable_prefix"]["estimated_tokens_min"] > 0


def test_cache_probe_release_gate_requires_clean_commit_and_five_requests() -> None:
    args = argparse.Namespace(repeat=5, execution_order="p0-first")

    assert cli._cache_probe_release_gate_error(args, "abc123", False) == ""
    assert "uncommitted" in cli._cache_probe_release_gate_error(args, "abc123", True)
    assert "--repeat 5" in cli._cache_probe_release_gate_error(
        argparse.Namespace(repeat=4),
        "abc123",
        False,
    )
    assert "--execution-order" in cli._cache_probe_release_gate_error(
        argparse.Namespace(repeat=5, execution_order=None),
        "abc123",
        False,
    )
    assert (
        cli._cache_probe_release_gate_error(
            argparse.Namespace(repeat=12, execution_order="p2-first"),
            "abc123",
            False,
            milestone="stable-v2.1.2",
        )
        == ""
    )
    assert "--repeat 12" in cli._cache_probe_release_gate_error(
        argparse.Namespace(repeat=5, execution_order="p1-first"),
        "abc123",
        False,
        milestone="stable-v2.1.2",
    )


def test_cache_report_does_not_write_failed_acceptance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_cache_probe_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "load_cache_suite_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        cli,
        "build_cache_ab_report",
        lambda rounds: {"status": "FAIL", "passed": False},
    )
    output_dir = tmp_path / "acceptance" / "stable-v2.1.1"

    exit_code = cli.cache_report_command(
        argparse.Namespace(
            p0_probe=[tmp_path / "p0-r1.json", tmp_path / "p0-r2.json"],
            p1_probe=[tmp_path / "p1-r1.json", tmp_path / "p1-r2.json"],
            p0_eval=[tmp_path / "p0-e1.json", tmp_path / "p0-e2.json"],
            p1_eval=[tmp_path / "p1-e1.json", tmp_path / "p1-e2.json"],
            output_dir=output_dir,
        )
    )

    assert exit_code == 1
    assert not output_dir.exists()


def test_cache_utilization_report_does_not_write_failed_acceptance(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "load_cache_probe_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "load_cache_suite_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        cli,
        "build_cache_utilization_report",
        lambda rounds: {
            "status": "FAIL",
            "passed": False,
            "criteria": {"all_rounds_passed": False},
            "rounds": [],
        },
    )
    output_dir = tmp_path / "acceptance" / "stable-v2.1.2"
    two = [tmp_path / "r1.json", tmp_path / "r2.json"]

    exit_code = cli.cache_utilization_report_command(
        argparse.Namespace(
            p1_probe=two,
            p2_probe=two,
            p1_eval=two,
            p2_eval=two,
            output_dir=output_dir,
        )
    )

    assert exit_code == 1
    assert not output_dir.exists()


def test_resume_command_uses_normal_settings_after_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".minicc" / "runs" / "run-1"
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    state = RunState.start(
        "resume",
        workspace_host_path=workspace,
        run_dir=run_dir,
        artifacts_dir=artifacts,
    )
    state.run_id = "run-1"
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="echo ok")
    state.approvals.append({"status": "approved", "action": "echo ok"})
    save_run_state(state)

    settings = Settings(
        provider=ProviderSettings(base_url="https://example.test/v1", api_key="key", model="model"),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(max_turns=2),
        context=ContextSettings(),
        policy=PolicySettings(),
    )
    loop_calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: FakeProvider())
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)

    def fake_build_loop(provider, executor, *, settings, session=None, state=None, max_turns=None, stream=None):
        loop_calls.append({"settings": settings, "state": state, "max_turns": max_turns})
        return FakeLoop(state)

    monkeypatch.setattr(cli, "_build_loop", fake_build_loop)

    exit_code = cli.resume_command(argparse.Namespace(run_id="run-1", execute_local=True))

    assert exit_code == 0
    assert loop_calls
    assert loop_calls[0]["settings"] is settings


def test_resume_command_denial_terminates_without_agent_loop(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".minicc" / "runs" / "run-denied"
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    state = RunState.start(
        "resume denied",
        workspace_host_path=workspace,
        run_dir=run_dir,
        artifacts_dir=artifacts,
    )
    state.run_id = "run-denied"
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="rm -r tmp_build")
    state.approvals.append({"status": "denied", "reason": "too risky", "action": "rm -r tmp_build"})
    save_run_state(state)

    settings = Settings(
        provider=ProviderSettings(base_url="https://example.test/v1", api_key="key", model="model"),
        sandbox=SandboxSettings(),
        budget=BudgetSettings(max_turns=2),
        context=ContextSettings(),
        policy=PolicySettings(),
    )
    loop_calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_build_provider_or_print_error", lambda loaded: FakeProvider())
    monkeypatch.setattr(cli, "LocalCommandExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_build_loop", lambda *args, **kwargs: loop_calls.append(kwargs))

    exit_code = cli.resume_command(argparse.Namespace(run_id="run-denied", execute_local=True))

    assert exit_code == 1
    assert loop_calls == []
    saved = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["pending_action"] is None
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "approval_resolved" in trace_text
    assert "denied" in trace_text


def test_release_gate_requires_clean_docker_commit_and_repeat_matrix() -> None:
    valid = argparse.Namespace(execute_local=False, repeat=3, case_names=["C01", "C02"])

    assert cli._release_gate_error(valid, "abc123", False) == ""
    assert "uncommitted" in cli._release_gate_error(valid, "abc123", True)
    assert "Docker" in cli._release_gate_error(
        argparse.Namespace(execute_local=True, repeat=3, case_names=["C01"]),
        "abc123",
        False,
    )
    assert "--repeat 3" in cli._release_gate_error(
        argparse.Namespace(execute_local=False, repeat=2, case_names=["C01"]),
        "abc123",
        False,
    )


def test_v212_release_gate_locks_canonical_suite_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    valid = argparse.Namespace(
        execute_local=False,
        repeat=3,
        case_names=["C02_fix_failing_test", "C07_large_log_debugging"],
        path=tmp_path / "other-suite",
    )

    assert "capability_suite_v1" in cli._release_gate_error(
        valid,
        "abc123",
        False,
        milestone="v2.1.2-development",
    )
    valid.path = tmp_path / "eval_cases" / "capability_suite_v1"
    assert (
        cli._release_gate_error(
            valid,
            "abc123",
            False,
            milestone="v2.1.2-development",
        )
        == ""
    )
    valid.repeat = 4
    assert "exactly --repeat 3" in cli._release_gate_error(
        valid,
        "abc123",
        False,
        milestone="v2.1.2-development",
    )


def test_v212_eval_rejects_external_fixture_before_provider(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    suite_root = tmp_path / "eval_cases" / "capability_suite_v1"
    for name in ("C02_fix_failing_test", "C07_large_log_debugging"):
        case_dir = suite_root / name
        fixture = tmp_path / "outside" / name
        case_dir.mkdir(parents=True)
        fixture.mkdir(parents=True)
        (fixture / "README.md").write_text("external\n", encoding="utf-8")
        (case_dir / "case.yaml").write_text(
            f"name: {name}\n"
            "prompt: Finish.\n"
            f"fixture: ../../../outside/{name}\n"
            "assertions: []\n",
            encoding="utf-8",
        )
    settings = Settings(
        provider=ProviderSettings(
            base_url="https://example.test/v1",
            api_key="key",
            model="model",
        ),
        sandbox=SandboxSettings(
            image="python@sha256:" + ("a" * 64),
        ),
        budget=BudgetSettings(max_turns=2),
        context=ContextSettings(),
        policy=PolicySettings(),
    )
    provider_calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_git_evidence", lambda cwd: ("abc123", False))
    monkeypatch.setattr(
        cli,
        "_build_provider_or_print_error",
        lambda loaded: provider_calls.append(loaded) or FakeProvider(),
    )

    exit_code = cli.eval_command(
        argparse.Namespace(
            path=suite_root,
            milestone="v2.1.2-development",
            execute_local=False,
            repeat=3,
            output_dir=None,
            case_names=["C02_fix_failing_test", "C07_large_log_debugging"],
            release_gate=True,
            cache_variant="p1",
            cache_sequence_id="external-fixture",
            execution_order="p1-first",
        )
    )

    assert exit_code == 2
    assert provider_calls == []


def test_committed_authority_profile_detects_skip_worktree_change(
    tmp_path,
) -> None:
    suite_root = tmp_path / "eval_cases" / "capability_suite_v1"
    for name in ("C02_fix_failing_test", "C07_large_log_debugging"):
        case_dir = suite_root / name
        fixture = case_dir / "fixture"
        fixture.mkdir(parents=True)
        (fixture / "input.txt").write_text(f"{name}\n", encoding="utf-8")
        (case_dir / "case.yaml").write_text(
            f"name: {name}\nprompt: Finish.\nassertions: []\n",
            encoding="utf-8",
        )
    for args in (
        ("init",),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "-m", "fixture baseline"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    cases = cli.discover_cases(suite_root)
    live = cli.build_case_authority_profiles(cases, project_root=tmp_path)
    committed = cli._committed_case_authority_profiles(
        live,
        project_root=tmp_path,
        git_commit=git_commit,
    )
    assert committed == live

    extra_empty_dir = (
        suite_root
        / "C07_large_log_debugging"
        / "fixture"
        / "untracked-empty"
    )
    extra_empty_dir.mkdir()
    with pytest.raises(ValueError, match="directory inventory"):
        cli._committed_case_authority_profiles(
            live,
            project_root=tmp_path,
            git_commit=git_commit,
        )
    extra_empty_dir.rmdir()

    changed_path = (
        "eval_cases/capability_suite_v1/"
        "C07_large_log_debugging/fixture/input.txt"
    )
    subprocess.run(
        ["git", "update-index", "--skip-worktree", changed_path],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    assert "skip-worktree" in cli._git_index_flags_error(tmp_path)
    (tmp_path / changed_path).write_text("changed\n", encoding="utf-8")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    changed_live = cli.build_case_authority_profiles(
        cli.discover_cases(suite_root),
        project_root=tmp_path,
    )

    assert status == ""
    assert changed_live != committed


def test_git_formal_state_rejects_ambient_content_transform_attributes(
    tmp_path,
) -> None:
    source = tmp_path / "src" / "runtime.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 'committed'\n", encoding="utf-8")
    for args in (
        ("init",),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "-m", "runtime baseline"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

    assert cli._git_transform_attributes_error(tmp_path) == ""
    info_attributes = tmp_path / ".git" / "info" / "attributes"
    info_attributes.write_text(
        "src/runtime.py filter=lossy\n",
        encoding="utf-8",
    )

    assert subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""
    assert "filter=lossy" in cli._git_transform_attributes_error(tmp_path)


def test_cleanup_command_defaults_to_dry_run_and_apply_uses_same_candidate(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".minicc" / "runs" / "old-run"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        '{"run_id":"old-run","goal":"old","status":"failed"}',
        encoding="utf-8",
    )
    old = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(run_dir, (old, old))

    assert cli.cleanup_command(argparse.Namespace(older_than_hours=24, apply=False)) == 0
    assert run_dir.exists()
    assert cli.cleanup_command(argparse.Namespace(older_than_hours=24, apply=True)) == 0
    assert not run_dir.exists()


def test_cli_version_matches_v22_development_package() -> None:
    assert __version__ == "2.2.0.dev0"
