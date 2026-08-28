from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from minicc.core.action_handler import ActionHandler
from minicc.core.checkpoint import CheckpointManager
from minicc.core.context import ContextBuilder, state_snapshot_text
from minicc.core.lifecycle import RunLifecycle
from minicc.core.protocol import (
    AskAction,
    BashAction,
    CodeModeAction,
    FinalAction,
    SkillAction,
    ToolCall,
    ToolCallBatch,
)
from minicc.core.provider import (
    CONTEXT_OVERFLOW,
    CompletionOptions,
    ModelProvider,
    ProviderError,
)
from minicc.core.runner import ModelTurn, ModelTurnConfig, ModelTurnRunner
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.core.tooling import ToolCallScheduler
from minicc.core.verification import CompletionVerifier
from minicc.policy.base import PolicyChain
from minicc.trace.recorder import TraceRecorder


class BashExecutor(Protocol):
    def run(self, action: BashAction, state: RunState) -> Observation:
        ...


class TurnProvider(Protocol):
    """回合一层抽象：AgentLoop 只依赖这个接口，不关心重试/降级编排。

    V4.1 把重试（core/retry.py）与最外层降级（core/failover.py）做成两个独立的
    编排层，它们都满足本接口；没有编排时用 ``DirectTurnProvider`` 直连 runner 的
    单次 attempt。
    """

    def next_turn(self, state: RunState, messages: list[dict[str, str]]) -> ModelTurn:
        ...


class DirectTurnProvider:
    """默认 TurnProvider：单次 attempt 直连 runner，不做重试/降级。"""

    def __init__(self, runner: ModelTurnRunner) -> None:
        self._runner = runner

    def next_turn(self, state: RunState, messages: list[dict[str, str]]) -> ModelTurn:
        return self._runner.next_turn(state, messages)


@dataclass(frozen=True)
class LoopConfig:
    max_seconds: int = 0
    max_turns: int = 0
    max_action_timeout_sec: int = 120
    model_options: CompletionOptions = field(default_factory=CompletionOptions)
    interrupt_after_steps: int | None = None
    max_parallel_tool_calls: int = 4
    max_tool_calls_per_step: int = 16


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
        context_builder: ContextBuilder | None = None,
        policy_chain: PolicyChain | None = None,
        session: SessionManager | None = None,
        trace: TraceRecorder | None = None,
        config: LoopConfig | None = None,
        checkpoint_manager: CheckpointManager | None = None,
        completion_verifier: CompletionVerifier | None = None,
        tool_scheduler: ToolCallScheduler | None = None,
        turn_provider_factory: Callable[[ModelTurnRunner], TurnProvider] | None = None,
    ) -> None:
        self.config = config or LoopConfig()
        self.session = session or SessionManager()
        self.context_builder = context_builder or ContextBuilder()
        self.trace = trace or TraceRecorder()
        self.context_builder.trace = self.trace
        self.lifecycle = RunLifecycle(self.trace)
        self.checkpoint_manager = checkpoint_manager
        self.turn_runner = ModelTurnRunner(
            provider,
            config=ModelTurnConfig(
                max_action_timeout_sec=self.config.max_action_timeout_sec,
                model_options=self.config.model_options,
                max_tool_calls_per_step=self.config.max_tool_calls_per_step,
            ),
            trace=self.trace,
        )
        # 回合抽象：默认直连 runner；retry/failover 执行器经 ``turn_provider_factory``
        # 在构造 runner 之后注入，仍复用同一个 runner 的协议解析/指标累计。
        self.turn_provider: TurnProvider = (
            turn_provider_factory(self.turn_runner)
            if turn_provider_factory is not None
            else DirectTurnProvider(self.turn_runner)
        )
        self.action_handler = ActionHandler(
            executor,
            policy_chain=policy_chain,
            session=self.session,
            trace=self.trace,
            completion_verifier=completion_verifier,
            skill_registry=self.context_builder.skill_registry,
            tool_scheduler=tool_scheduler,
            code_mode_timeout_sec=self.config.max_action_timeout_sec,
        )
        self.tool_scheduler = tool_scheduler
        if self.tool_scheduler is not None:
            runner = self.tool_scheduler.runner
            if hasattr(runner, "action_handler"):
                runner.action_handler = self.action_handler

    def run(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep] | None = None,
    ) -> AgentLoopResult:
        resumed = trajectory is not None
        trajectory = list(trajectory or [])
        state.metrics["max_parallel_tool_calls"] = self.config.max_parallel_tool_calls
        state.metrics["max_tool_calls_per_step"] = self.config.max_tool_calls_per_step
        if resumed:
            self.lifecycle.resume(state, len(trajectory))
        else:
            self.lifecycle.start(state)
        self._checkpoint(state, trajectory, "resume_started" if resumed else "run_started")
        if self._should_interrupt(trajectory):
            self._interrupt(state, trajectory)

        started_at = time.monotonic()
        while state.status == "running":
            if self.config.max_seconds > 0 and (time.monotonic() - started_at) >= self.config.max_seconds:
                self.session.fail(state, "Run failed because max_seconds was exhausted.")
                break

            if self.config.max_turns > 0 and int(state.metrics.get("turns", 0)) >= self.config.max_turns:
                self.session.fail(state, "Run failed because max_turns was exhausted.")
                break

            self.context_builder.maybe_compact(state, trajectory)
            messages = self.context_builder.build_messages(state, trajectory)
            try:
                turn = self.turn_provider.next_turn(state, messages)
            except ProviderError as exc:
                state.metrics["provider_errors"] = state.metrics.get("provider_errors", 0) + 1
                state.metrics["provider_last_error_code"] = exc.failure.code
                self.trace.record("provider_error", state, error=str(exc), code=exc.failure.code)
                if exc.failure.code == CONTEXT_OVERFLOW:
                    retries = int(state.metrics.get("context_overflow_retries", 0))
                    if retries < self.context_builder.config.max_overflow_retries:
                        if self.context_builder.force_compact(state, trajectory):
                            state.metrics["context_overflow_retries"] = retries + 1
                            state.metrics["context_overflow_recovered"] = (
                                state.metrics.get("context_overflow_recovered", 0) + 1
                            )
                            self.trace.record(
                                "context_overflow_recovery",
                                state,
                                retry=retries + 1,
                            )
                            continue
                self.session.fail(state, f"Run failed because the model provider failed: {exc}")
                break
            if turn.observation is not None:
                trajectory.append(
                    TrajectoryStep(
                        action=None,
                        observation=turn.observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                )
                self._checkpoint(state, trajectory, "model_observation_recorded")

            if not turn.should_continue or state.status != "running":
                break

            if not turn.actions:
                continue

            control_actions = [
                action
                for action in turn.actions
                if isinstance(action, (FinalAction, AskAction, SkillAction, CodeModeAction))
            ]
            tool_calls = [action for action in turn.actions if isinstance(action, ToolCall)]

            if control_actions and len(turn.actions) > 1:
                # The model mixed a control tool (final/ask/skill/code_mode) with other
                # tool calls in the same turn — a run-time-recoverable contract
                # violation (was a parse-time ProtocolError under the old text-JSON
                # protocol; here it becomes a non-terminal feedback observation).
                observation = Observation(
                    kind="protocol_error",
                    message=(
                        "final/ask/skill/code_mode must each be the only call in their turn; "
                        "this turn mixed a control tool with other tool calls."
                    ),
                )
                trajectory.append(
                    TrajectoryStep(action=None, observation=observation, state_snapshot=state_snapshot_text(state))
                )
                self.trace.observation_created(state, observation)
                continue

            if control_actions:
                action = control_actions[0]
                outcome = self.action_handler.handle(action, state)
                trajectory.extend(outcome.steps)
                self.session.save(state)
                reason = "action_completed"
                current_status = str(state.status)
                if current_status == "waiting_approval":
                    reason = "waiting_approval"
                elif current_status == "completed":
                    reason = "run_completed"
                self._checkpoint(state, trajectory, reason)

                if state.status == "running" and self._should_interrupt(trajectory):
                    self._interrupt(state, trajectory)

                if not outcome.should_continue:
                    break
                continue

            if self.tool_scheduler is None:
                self.session.fail(state, "Run failed because no tool scheduler is configured to dispatch tool calls.")
                break

            for model_order, call in enumerate(tool_calls):
                self.trace.tool_call(
                    state,
                    call_id=call.id,
                    tool=call.tool,
                    arguments=dict(call.arguments),
                    model_order=model_order,
                    execution_mode="parallel" if call.tool == "read" else "exclusive",
                )
            results = self.tool_scheduler.dispatch(tuple(tool_calls), state)
            state.metrics["tool_call_steps"] = state.metrics.get("tool_call_steps", 0) + 1
            state.metrics["tool_calls"] = state.metrics.get("tool_calls", 0) + len(results)
            state.metrics["max_parallel_tool_calls"] = self.config.max_parallel_tool_calls
            ordered_payload = []
            for result in results:
                self.trace.tool_result(state, result)
                ordered_payload.append(
                    {
                        "call_id": result.call_id,
                        "tool": result.tool,
                        "model_order": result.model_order,
                        "is_error": result.is_error,
                        "content": result.content,
                    }
                )
                metric = f"{result.tool}_tool_calls"
                state.metrics[metric] = state.metrics.get(metric, 0) + 1
            observation = Observation(
                kind=(
                    "command_error" if any(result.is_error for result in results) else "command_result"
                ),
                exit_code=(None if any(result.is_error for result in results) else 0),
                stdout_preview=json.dumps(ordered_payload, ensure_ascii=False),
                message="Ordered tool results.",
                duration_ms=sum(result.duration_ms for result in results),
            )
            trajectory.append(
                TrajectoryStep(
                    action=ToolCallBatch(calls=tuple(tool_calls)),
                    observation=observation,
                    state_snapshot=state_snapshot_text(state),
                )
            )
            state.last_observation = observation
            self.session.save(state)
            self._checkpoint(state, trajectory, "tool_calls_completed")

            if state.status == "running" and self._should_interrupt(trajectory):
                self._interrupt(state, trajectory)

        self.lifecycle.finish(state)
        if state.status == "interrupted":
            self._checkpoint(state, trajectory, "interrupted_finalized")
        if state.run_dir is not None or self.session.runs_root is not None:
            self.session.save(state)
        return AgentLoopResult(state=state, trajectory=trajectory)

    def _checkpoint(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
        reason: str,
    ) -> None:
        if self.checkpoint_manager is not None:
            self.checkpoint_manager.create(state, trajectory, reason=reason)

    def _should_interrupt(self, trajectory: list[TrajectoryStep]) -> bool:
        threshold = self.config.interrupt_after_steps
        return threshold is not None and len(trajectory) >= threshold

    def _interrupt(self, state: RunState, trajectory: list[TrajectoryStep]) -> None:
        state.status = "interrupted"
        state.metrics["interrupted_after_steps"] = len(trajectory)
        self._checkpoint(state, trajectory, "controlled_interrupt")
        self.session.save(state)


class DisabledExecutor:
    """Executor used by tests or callers that want to block command execution."""

    def run(self, action: BashAction, state: RunState) -> Observation:
        return Observation(
            kind="policy_violation",
            message=(
                "Command execution is disabled for this run. "
                "Use a DockerCommandExecutor or LocalCommandExecutor to execute bash actions."
            ),
            stderr_preview=action.command,
        )
