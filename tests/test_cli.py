import argparse
import json
import os
from datetime import datetime, timezone

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
