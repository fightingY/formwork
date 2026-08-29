from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicc.core.protocol import BashAction, CodeModeAction
from minicc.core.state import Observation, RunState
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.code_mode import CodeModeResult, inject_facade, run_code_mode_script
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
        if sys.platform != "win32":
            # --cap-drop ALL removes CAP_DAC_OVERRIDE, so even root inside is
            # bound by host ownership and cannot write owner-owned workspaces
            # (verified on GitHub runners: root EACCES, owner works). Run as
            # the invoking user so sandbox access matches the workspace owner.
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
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
        try:
            inject_facade(container_name)
        except Exception:
            # code_mode degrades to unavailable (ActionHandler reports a
            # recoverable error) if the facade can't be written; the rest of
            # the run (read/edit/write/bash) is unaffected.
            pass
        return container_name

    def exec(
        self,
        *,
        container_name: str,
        action: BashAction,
        cancel_event: object | None = None,
    ) -> CommandResult:
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
            completed, cancelled = _run_cancellable(
                command,
                timeout=action.timeout_sec,
                cancel_event=cancel_event,
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

        if cancelled:
            return CommandResult(
                exit_code=None,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
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


def _run_cancellable(
    args: list[str], *, timeout: int, cancel_event: object | None
) -> tuple[subprocess.CompletedProcess[str], bool]:
    if cancel_event is None:
        return (
            subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            ),
            False,
        )
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    started = time.monotonic()
    try:
        while True:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr), True
            if timeout > 0 and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr), False
            except subprocess.TimeoutExpired:
                continue
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr) from None



class DockerCommandExecutor:
    def __init__(
        self,
        runner: DockerSandboxRunner,
        *,
        artifacts: ArtifactStore,
        artifact_threshold_bytes: int = 50_000,
        preview_chars: int = 12_000,
        restart_params: dict[str, object] | None = None,
    ) -> None:
        self.runner = runner
        self.artifacts = artifacts
        self.artifact_threshold_bytes = artifact_threshold_bytes
        self.preview_chars = preview_chars
        # Lazy-restart params (run_id/workspace_dir/artifacts_dir/writable_paths) captured
        # at construction time from the same values used by the original runner.start()
        # call. A command timeout no longer hard-fails the run — the container is torn
        # down and this dict lets the *next* bash call transparently start a fresh one.
        self.restart_params = restart_params

    def run(self, action: BashAction, state: RunState) -> Observation:
        if not state.container_name:
            if self.restart_params is None:
                return Observation(
                    kind="command_error",
                    message="Docker container is not started for this run.",
                    stderr_preview=action.command,
                )
            state.container_name = self.runner.start(**self.restart_params)  # type: ignore[arg-type]

        try:
            result = self.runner.exec(
                container_name=state.container_name,
                action=action,
                cancel_event=getattr(state, "_cancel_token", None),
            )
        except TypeError as exc:
            # Third-party/test runners may still implement the pre-token
            # contract; the built-in runner always accepts the token.
            if "cancel_event" not in str(exc):
                raise
            result = self.runner.exec(container_name=state.container_name, action=action)
        if result.timed_out:
            # Non-terminal: the container was torn down by runner.exec()'s own cleanup()
            # on timeout, but the run keeps going. is_error normalization for this
            # observation (partial output + timeout notice, is_error=False) happens
            # downstream in core/tooling.py. The next bash call lazily restarts the
            # container above.
            state.container_name = None
        return observation_from_command_result(
            result,
            state=state,
            artifacts=self.artifacts,
            artifact_threshold_bytes=self.artifact_threshold_bytes,
            preview_chars=self.preview_chars,
        )

    def run_code_mode(
        self,
        action: CodeModeAction,
        state: RunState,
        *,
        timeout_sec: int,
        dispatch: Callable[[str, dict[str, Any]], dict[str, Any]],
    ) -> Observation:
        """Run a Code Mode script via ``sandbox.code_mode``, reusing the same
        lazy-restart contract as ``run()`` — a script-level timeout tears the
        container down (handled inside ``run_code_mode_script``'s watchdog kill)
        and the next bash/code_mode call restarts it, same as a bash timeout."""
        if not state.container_name:
            if self.restart_params is None:
                return Observation(kind="command_error", message="Docker container is not started for this run.")
            state.container_name = self.runner.start(**self.restart_params)  # type: ignore[arg-type]

        result = run_code_mode_script(
            container_name=state.container_name,
            script=action.script,
            timeout_sec=timeout_sec,
            dispatch=dispatch,
        )
        if result.timed_out:
            self.runner.cleanup(state.container_name)
            state.container_name = None
        return _observation_from_code_mode_result(result)


def _observation_from_code_mode_result(result: CodeModeResult) -> Observation:
    payload = {
        "script_exit_code": result.script_exit_code,
        "script_stdout": result.script_stdout,
        "tool_calls_made": list(result.tool_calls_made),
    }
    if result.timed_out:
        payload["timeout_notice"] = (
            "Script timed out; output above is whatever tool calls and stdout were "
            "produced before it was stopped."
        )
        return Observation(
            kind="timeout",
            stdout_preview=json.dumps(payload, ensure_ascii=False),
            message="Code Mode script timed out.",
        )
    if result.is_error:
        payload["traceback"] = result.traceback_text
        return Observation(
            kind="command_error",
            exit_code=result.script_exit_code,
            stdout_preview=json.dumps(payload, ensure_ascii=False),
            message=f"Code Mode script failed with exit code {result.script_exit_code}.",
        )
    return Observation(
        kind="command_result",
        exit_code=0,
        stdout_preview=json.dumps(payload, ensure_ascii=False),
        message="Code Mode script exited successfully.",
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
        _grant_container_write_access(target)
        return target
    if relative_path.endswith("/"):
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    _grant_container_write_access(target)
    return target


def _grant_container_write_access(path: Path) -> None:
    # Containers run with --cap-drop ALL, so even root inside is bound by host
    # permission bits. Linux bind mounts enforce them (GitHub runners), while
    # Docker Desktop on Windows masks ownership — make declared writable paths
    # writable for any container uid on both.
    path.chmod(0o777 if path.is_dir() else 0o666)
