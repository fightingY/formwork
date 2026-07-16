import subprocess
from pathlib import Path

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.docker_runner import DockerCommandExecutor, DockerSandboxConfig, DockerSandboxRunner
from minicc.sandbox.observation import CommandResult


def test_docker_runner_start_uses_restricted_container_args(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = DockerSandboxRunner(DockerSandboxConfig(image="python:test"))

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    container_name = runner.start(
        run_id="abc123",
        workspace_dir=tmp_path,
        artifacts_dir=artifacts_dir,
    )

    assert container_name == "minicc-abc123"
    command = calls[0][0]
    assert command[:3] == ["docker", "run", "-d"]
    assert "--network" in command
    assert "none" in command
    assert "--cap-drop" in command
    assert "ALL" in command
    assert "--security-opt" in command
    assert "no-new-privileges" in command
    assert "python:test" in command
    assert any(str(tmp_path.resolve()) in item for item in command)
    assert any("target=/workspace/.minicc_artifacts,readonly" in item for item in command)
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"
    assert (tmp_path / ".minicc_artifacts").is_dir()


def test_docker_runner_mounts_readonly_root_and_only_declared_writable_paths(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = DockerSandboxRunner()

    runner.start(
        run_id="guarded",
        workspace_dir=tmp_path,
        writable_paths=("src/", "ONBOARDING.md"),
    )

    mounts = [calls[0][index + 1] for index, value in enumerate(calls[0]) if value == "--mount"]
    assert any("target=/workspace,readonly" in mount for mount in mounts)
    assert any("target=/workspace/src/" in mount and "readonly" not in mount for mount in mounts)
    assert any("target=/workspace/ONBOARDING.md" in mount and "readonly" not in mount for mount in mounts)
    assert (tmp_path / "ONBOARDING.md").exists()


def test_docker_runner_exec_uses_bash_lc(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = DockerSandboxRunner()

    result = runner.exec(
        container_name="minicc-run",
        action=BashAction(command="pytest -q", timeout_sec=9),
    )

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert calls[0][0] == [
        "docker",
        "exec",
        "--workdir",
        "/workspace",
        "minicc-run",
        "bash",
        "-lc",
        "pytest -q",
    ]
    assert calls[0][1]["timeout"] == 9
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"


def test_docker_command_executor_creates_observation(tmp_path) -> None:
    class FakeRunner:
        def exec(self, *, container_name: str, action: BashAction) -> CommandResult:
            return CommandResult(exit_code=0, stdout="ok")

    state = RunState.start("run command")
    state.container_name = "minicc-run"
    executor = DockerCommandExecutor(FakeRunner(), artifacts=ArtifactStore(tmp_path))

    observation = executor.run(BashAction(command="echo ok"), state)

    assert observation.kind == "command_result"
    assert observation.stdout_preview == "ok"


def test_docker_command_executor_requires_started_container(tmp_path) -> None:
    state = RunState.start("run command")
    executor = DockerCommandExecutor(DockerSandboxRunner(), artifacts=ArtifactStore(tmp_path))

    observation = executor.run(BashAction(command="echo ok"), state)

    assert observation.kind == "command_error"
    assert "not started" in observation.message
