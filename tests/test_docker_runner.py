import os
import subprocess
import sys

import pytest

from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.docker_runner import (
    DockerCommandExecutor,
    DockerSandboxConfig,
    DockerSandboxError,
    DockerSandboxRunner,
    check_docker_ready,
)
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
    command = next(command for command, _ in calls if command[:3] == ["docker", "run", "-d"])
    assert command[:3] == ["docker", "run", "-d"]
    assert "--network" in command
    assert "none" in command
    assert "--cap-drop" in command
    assert "ALL" in command
    assert "--security-opt" in command
    assert "no-new-privileges" in command
    if sys.platform != "win32":
        # Sandbox must run as the workspace owner: with --cap-drop ALL even
        # root is bound by host ownership (GitHub runners reproduced EACCES).
        user_index = command.index("--user")
        assert command[user_index + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "python:test" in command
    assert any(str(tmp_path.resolve()) in item for item in command)
    assert any("target=/workspace/.minicc_artifacts,readonly" in item for item in command)
    run_kwargs = next(kwargs for command, kwargs in calls if command[:3] == ["docker", "run", "-d"])
    assert run_kwargs["encoding"] == "utf-8"
    assert run_kwargs["errors"] == "replace"
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

    command = next(command for command in calls if command[:3] == ["docker", "run", "-d"])
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert any("target=/workspace,readonly" in mount for mount in mounts)
    assert any("target=/workspace/src/" in mount and "readonly" not in mount for mount in mounts)
    assert any("target=/workspace/ONBOARDING.md" in mount and "readonly" not in mount for mount in mounts)
    assert (tmp_path / "ONBOARDING.md").exists()
    # Declared writable paths must be writable for any container uid: the
    # sandbox drops all capabilities, so host permission bits are enforced.
    assert os.stat(tmp_path / "ONBOARDING.md").st_mode & 0o222 == 0o222
    assert os.stat(tmp_path / "src").st_mode & 0o222 == 0o222


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


def test_docker_runner_start_rolls_back_failed_container(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(125, command, stderr="mount failed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = DockerSandboxRunner()

    with pytest.raises(subprocess.CalledProcessError):
        runner.start(run_id="failed-start", workspace_dir=tmp_path)

    assert ["docker", "rm", "-f", "minicc-failed-start"] in calls


def test_docker_runner_timeout_removes_container(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "exec"]:
            raise subprocess.TimeoutExpired(command, 1, output="partial", stderr="stuck")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = DockerSandboxRunner().exec(
        container_name="minicc-timeout",
        action=BashAction(command="sleep 30", timeout_sec=1),
    )

    assert result.timed_out is True
    assert ["docker", "rm", "-f", "minicc-timeout"] in calls


def test_docker_command_executor_timeout_is_non_terminal(tmp_path) -> None:
    class FakeRunner:
        def exec(self, *, container_name: str, action: BashAction) -> CommandResult:
            return CommandResult(exit_code=None, timed_out=True, timeout_sec=1)

    state = RunState.start("run command")
    state.container_name = "minicc-run"
    executor = DockerCommandExecutor(FakeRunner(), artifacts=ArtifactStore(tmp_path))

    observation = executor.run(BashAction(command="sleep 30", timeout_sec=1), state)

    assert observation.kind == "timeout"
    assert state.container_name is None
    assert state.status == "running"


def test_docker_command_executor_lazily_restarts_after_timeout(tmp_path) -> None:
    started_with: list[dict[str, object]] = []

    class FakeRunner:
        def __init__(self) -> None:
            self.calls = 0

        def start(self, **kwargs: object) -> str:
            started_with.append(kwargs)
            return "minicc-run-2"

        def exec(self, *, container_name: str, action: BashAction) -> CommandResult:
            self.calls += 1
            if self.calls == 1:
                return CommandResult(exit_code=None, timed_out=True, timeout_sec=1)
            assert container_name == "minicc-run-2"
            return CommandResult(exit_code=0, stdout="ok", timed_out=False, timeout_sec=1)

    fake_runner = FakeRunner()
    state = RunState.start("run command")
    state.container_name = "minicc-run"
    restart_params = {"run_id": "run-1", "workspace_dir": tmp_path}
    executor = DockerCommandExecutor(
        fake_runner,
        artifacts=ArtifactStore(tmp_path),
        restart_params=restart_params,
    )

    timeout_observation = executor.run(BashAction(command="sleep 30", timeout_sec=1), state)
    assert timeout_observation.kind == "timeout"
    assert state.container_name is None

    next_observation = executor.run(BashAction(command="echo ok", timeout_sec=1), state)

    assert started_with == [restart_params]
    assert state.container_name == "minicc-run-2"
    assert next_observation.kind == "command_result"


def test_check_docker_ready_reports_missing_cli(monkeypatch) -> None:
    monkeypatch.setattr("minicc.sandbox.docker_runner.shutil.which", lambda name: None)

    with pytest.raises(DockerSandboxError, match="Docker CLI was not found"):
        check_docker_ready()
