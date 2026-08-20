from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.observation import CommandResult, observation_from_command_result


class DockerSandboxError(RuntimeError):
    """Raised when the Docker runtime cannot be used for a sandbox run."""


@dataclass(frozen=True)
class DockerSandboxConfig:
    image: str = "python:3.11-slim"
    cpus: str = "1"
    memory: str = "1g"
    pids_limit: int = 256
    network: str = "none"


class DockerSandboxRunner:
    def __init__(self, config: DockerSandboxConfig | None = None) -> None:
        self.config = config or DockerSandboxConfig()

    def start(
        self,
        *,
        run_id: str,
        workspace_dir: Path,
        artifacts_dir: Path | None = None,
        writable_paths: tuple[str, ...] | None = None,
    ) -> str:
        container_name = f"minicc-{run_id}"
        check_docker_ready()
        if artifacts_dir is not None:
            # The workspace root may be mounted read-only. Docker cannot create
            # a nested mountpoint inside it during container initialization, so
            # prepare the ignored mountpoint on the host first.
            (workspace_dir / ".minicc_artifacts").mkdir(parents=True, exist_ok=True)
        command = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--workdir",
            "/workspace",
            "--mount",
            (
                f"type=bind,source={workspace_dir.resolve()},target=/workspace"
                + (",readonly" if writable_paths is not None else "")
            ),
        ]
        for relative_path in writable_paths or ():
            source = _prepare_writable_mount(workspace_dir, relative_path)
            command.extend(
                [
                    "--mount",
                    f"type=bind,source={source},target=/workspace/{relative_path}",
                ]
            )
        if artifacts_dir is not None:
            command.extend(
                [
                    "--mount",
                    f"type=bind,source={artifacts_dir.resolve()},target=/workspace/.minicc_artifacts,readonly",
                ]
            )
        command.extend(
            [
            "--network",
            self.config.network,
            "--cpus",
            self.config.cpus,
            "--memory",
            self.config.memory,
            "--pids-limit",
            str(self.config.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            self.config.image,
            "sleep",
            "infinity",
            ]
        )
        try:
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=True,
            )
        except Exception:
            # Docker may create the container before failing during runtime
            # initialization. Roll back by name so a failed start is atomic.
            try:
                self.cleanup(container_name)
            except Exception:
                pass
            raise
        return container_name

    def exec(self, *, container_name: str, action: BashAction) -> CommandResult:
        started = time.perf_counter()
        command = [
            "docker",
            "exec",
            "--workdir",
            "/workspace",
            container_name,
            "bash",
            "-lc",
            action.command,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=action.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            self.cleanup(container_name)
            return CommandResult(
                exit_code=None,
                stdout=_decode_timeout_output(exc.stdout),
                stderr=_decode_timeout_output(exc.stderr),
                timed_out=True,
                timeout_sec=action.timeout_sec,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def cleanup(self, container_name: str | None) -> None:
        if not container_name:
            return
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )


class DockerCommandExecutor:
    def __init__(
        self,
        runner: DockerSandboxRunner,
        *,
        artifacts: ArtifactStore,
        artifact_threshold_bytes: int = 16 * 1024,
        preview_chars: int = 12_000,
    ) -> None:
        self.runner = runner
        self.artifacts = artifacts
        self.artifact_threshold_bytes = artifact_threshold_bytes
        self.preview_chars = preview_chars

    def run(self, action: BashAction, state: RunState) -> Observation:
        if not state.container_name:
            return Observation(
                kind="command_error",
                message="Docker container is not started for this run.",
                stderr_preview=action.command,
            )

        result = self.runner.exec(container_name=state.container_name, action=action)
        if result.timed_out:
            state.container_name = None
            state.status = "failed"
            state.state_summary = (
                "Run failed because the Docker sandbox was terminated after a command timeout."
            )
        return observation_from_command_result(
            result,
            state=state,
            artifacts=self.artifacts,
            artifact_threshold_bytes=self.artifact_threshold_bytes,
            preview_chars=self.preview_chars,
        )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def check_docker_ready(*, timeout_sec: int = 10) -> None:
    """Fail before model execution when the Docker CLI or daemon is unavailable."""
    if shutil.which("docker") is None:
        raise DockerSandboxError("Docker CLI was not found. Install Docker Desktop and try again.")
    try:
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=True,
        )
    except FileNotFoundError as exc:
        raise DockerSandboxError(
            "Docker CLI was not found. Install Docker Desktop and try again."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerSandboxError(
            "Docker daemon did not respond in time. Start or restart Docker Desktop."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise DockerSandboxError(
            f"Docker daemon is unavailable{suffix}. Start Docker Desktop and try again."
        ) from exc


def _prepare_writable_mount(workspace_dir: Path, relative_path: str) -> Path:
    root = workspace_dir.resolve()
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Writable path escapes workspace: {relative_path}")
    if target.exists():
        return target
    if relative_path.endswith("/"):
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    return target
