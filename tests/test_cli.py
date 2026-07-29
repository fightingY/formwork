import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

from minicc import cli
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
        "name: demo\nprompt: Finish.\nassertions: []\n",
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
        lambda loaded: FakeProvider(['{"type":"final","answer":"done"}']),
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
    args = cli.build_parser().parse_args(["eval", "--cache-variant", "p1"])

    assert args.cache_variant == "p1"


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
