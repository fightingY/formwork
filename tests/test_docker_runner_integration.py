from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from minicc.core.protocol import BashAction
from minicc.sandbox.docker_runner import DockerSandboxConfig, DockerSandboxRunner

pytestmark = pytest.mark.skipif(
    os.getenv("MINICC_DOCKER_INTEGRATION") != "1",
    reason="set MINICC_DOCKER_INTEGRATION=1 to run real Docker tests",
)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


pytestmark = [
    pytestmark,
    pytest.mark.skipif(not _docker_available(), reason="Docker daemon is unavailable"),
]


@pytest.fixture
def runner() -> DockerSandboxRunner:
    return DockerSandboxRunner(
        DockerSandboxConfig(
            image=os.getenv("MINICC_DOCKER_TEST_IMAGE", "python:3.11-slim"),
            network="none",
        )
    )


def test_real_container_exec_and_cleanup(runner, tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    name = runner.start(run_id="integration-exec", workspace_dir=tmp_path, artifacts_dir=artifacts)
    try:
        result = runner.exec(
            container_name=name,
            action=BashAction(command="printf integration-ok", timeout_sec=10),
        )
        assert result.exit_code == 0
        assert result.stdout == "integration-ok"
    finally:
        runner.cleanup(name)


def test_real_container_enforces_declared_writable_paths(runner, tmp_path) -> None:
    name = runner.start(
        run_id="integration-mounts",
        workspace_dir=tmp_path,
        writable_paths=("allowed.txt",),
    )
    try:
        allowed = runner.exec(
            container_name=name,
            action=BashAction(command="printf ok > allowed.txt", timeout_sec=10),
        )
        denied = runner.exec(
            container_name=name,
            action=BashAction(command="printf no > denied.txt", timeout_sec=10),
        )
        assert allowed.exit_code == 0
        assert denied.exit_code != 0
    finally:
        runner.cleanup(name)


def test_real_timeout_removes_container(runner, tmp_path) -> None:
    name = runner.start(run_id="integration-timeout", workspace_dir=tmp_path)
    result = runner.exec(
        container_name=name,
        action=BashAction(command="sleep 30", timeout_sec=1),
    )

    assert result.timed_out is True
    inspected = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10)
    assert inspected.returncode != 0
