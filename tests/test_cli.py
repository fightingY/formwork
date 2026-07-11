import argparse

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
        "artifacts/diff.patch",
        "run_report.json",
        "run_report.md",
    ]:
        assert (run_dir / relative_path).exists()


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
