from __future__ import annotations

import subprocess
import shutil
import time
from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.prompt import PromptBuilder
from minicc.core.protocol import Action, AskAction, BashAction, FinalAction, ProtocolError, parse_action
from minicc.core.provider import CompletionOptions, ModelProvider, ModelUsage
from minicc.core.state import Observation, RunState, TrajectoryStep


class BashExecutor(Protocol):
    def run(self, action: BashAction, state: RunState) -> Observation:
        ...


@dataclass(frozen=True)
class LoopConfig:
    max_turns: int = 8
    max_protocol_errors: int = 2
    max_action_timeout_sec: int = 120
    model_options: CompletionOptions = field(default_factory=CompletionOptions)


@dataclass
class AgentLoopResult:
    state: RunState
    trajectory: list[TrajectoryStep]


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        executor: BashExecutor,
        *,
        prompt_builder: PromptBuilder | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self.provider = provider
        self.executor = executor
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.config = config or LoopConfig()

    def run(self, state: RunState) -> AgentLoopResult:
        trajectory: list[TrajectoryStep] = []
        protocol_errors = 0

        while state.status == "running":
            if state.metrics["turns"] >= self.config.max_turns:
                state.status = "failed"
                state.state_summary = "Run failed because max_turns was exhausted."
                break

            messages = self.prompt_builder.build(state, trajectory)
            response = self.provider.complete(messages, options=self.config.model_options)
            state.metrics["turns"] += 1
            _accumulate_usage(state, response.usage, response.latency_ms)

            try:
                action = parse_action(
                    response.text,
                    max_timeout_sec=self.config.max_action_timeout_sec,
                )
                protocol_errors = 0
            except ProtocolError as exc:
                protocol_errors += 1
                state.metrics["protocol_errors"] += 1
                observation = Observation(
                    kind="protocol_error",
                    message=exc.message,
                    stderr_preview=exc.raw_text[:4000],
                )
                trajectory.append(TrajectoryStep(action=None, observation=observation))
                if protocol_errors > self.config.max_protocol_errors:
                    state.status = "failed"
                    state.state_summary = "Run failed because the model repeatedly violated the action protocol."
                    break
                continue

            if isinstance(action, FinalAction):
                state.status = "completed"
                state.final_answer = action.answer
                break

            if isinstance(action, AskAction):
                state.status = "waiting_approval"
                state.open_questions.append(action.question)
                break

            observation = self.executor.run(action, state)
            state.metrics["bash_actions"] += 1
            if observation.kind == "command_error":
                state.metrics["command_failures"] += 1
            elif observation.kind == "timeout":
                state.metrics["timeouts"] += 1
            trajectory.append(TrajectoryStep(action=action, observation=observation))

        return AgentLoopResult(state=state, trajectory=trajectory)


class DisabledExecutor:
    """M1 CLI default: keeps the loop testable without unsafe local execution."""

    def run(self, action: BashAction, state: RunState) -> Observation:
        return Observation(
            kind="policy_violation",
            message=(
                "Local command execution is disabled in the M1 CLI. "
                "Use --execute-local for a local demo, or wait for the M2 Docker sandbox."
            ),
            stderr_preview=action.command,
        )


class LocalCommandExecutor:
    """Small M1 executor used for demos and tests before Docker arrives in M2."""

    def run(self, action: BashAction, state: RunState) -> Observation:
        started = time.perf_counter()
        command_args = _local_shell_args(action.command)
        if command_args is None:
            return Observation(
                kind="policy_violation",
                stderr_preview=action.command,
                message=(
                    "Local command execution requested, but no bash executable was found. "
                    "M2 will execute commands inside a Docker Linux sandbox."
                ),
            )
        try:
            completed = subprocess.run(
                command_args,
                cwd=state.workspace_host_path,
                capture_output=True,
                text=True,
                timeout=action.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            return Observation(
                kind="timeout",
                stdout_preview=(exc.stdout or "")[:4000],
                stderr_preview=(exc.stderr or "")[:4000],
                message=f"Command timed out after {action.timeout_sec} seconds.",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode == 0 and not stdout and not stderr:
            return Observation(
                kind="no_output",
                exit_code=0,
                message="Command exited successfully with no output.",
                duration_ms=duration_ms,
            )
        if completed.returncode == 0:
            return Observation(
                kind="command_result",
                exit_code=0,
                stdout_preview=stdout[:4000],
                stderr_preview=stderr[:4000],
                message="Command exited successfully.",
                duration_ms=duration_ms,
            )
        return Observation(
            kind="command_error",
            exit_code=completed.returncode,
            stdout_preview=stdout[:4000],
            stderr_preview=stderr[:4000],
            message=f"Command failed with exit code {completed.returncode}.",
            duration_ms=duration_ms,
        )


def _accumulate_usage(state: RunState, usage: ModelUsage, latency_ms: int) -> None:
    metric_map = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cached_tokens": usage.cached_tokens,
        "prompt_cache_hit_tokens": usage.cache_hit_tokens,
        "prompt_cache_miss_tokens": usage.cache_miss_tokens,
    }
    for key, value in metric_map.items():
        if value is not None:
            state.metrics[key] += value
    state.metrics["latency_ms"] += latency_ms


def _local_shell_args(command: str) -> list[str] | None:
    bash_path = shutil.which("bash")
    if bash_path:
        return [bash_path, "-lc", command]
    return None
