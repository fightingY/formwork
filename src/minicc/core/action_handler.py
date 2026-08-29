from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from minicc.core.context import state_snapshot_text
from minicc.core.multi_agent import ChildTask, MultiAgentManager
from minicc.core.protocol import (
    Action,
    AskAction,
    BashAction,
    CodeModeAction,
    DelegateAction,
    FinalAction,
    SkillAction,
    ToolCall,
)
from minicc.core.session import (
    SessionManager,
    begin_execution,
    complete_execution,
    record_execution_metrics,
)
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.core.tooling import ToolCallScheduler
from minicc.core.verification import CompletionVerifier
from minicc.memory.working import ground_memory_references
from minicc.policy.base import PolicyChain, PolicyDecision
from minicc.skills.registry import SkillRegistry
from minicc.trace.recorder import TraceRecorder


class BashExecutor(Protocol):
    def run(self, action: BashAction, state: RunState) -> Observation:
        ...


@dataclass
class ActionOutcome:
    steps: list[TrajectoryStep] = field(default_factory=list)
    should_continue: bool = True


class ActionHandler:
    def __init__(
        self,
        executor: BashExecutor,
        *,
        policy_chain: PolicyChain | None = None,
        session: SessionManager | None = None,
        trace: TraceRecorder | None = None,
        completion_verifier: CompletionVerifier | None = None,
        skill_registry: SkillRegistry | None = None,
        tool_scheduler: ToolCallScheduler | None = None,
        code_mode_timeout_sec: int = 120,
        multi_agent_manager: MultiAgentManager | None = None,
    ) -> None:
        self.executor = executor
        self.policy_chain = policy_chain or PolicyChain()
        self.session = session or SessionManager()
        self.trace = trace
        self.completion_verifier = completion_verifier
        self.skill_registry = skill_registry
        # Code Mode reuses the same ToolCallScheduler/policy/executor path as
        # ordinary tool_calls dispatch — set by AgentLoop after both are built,
        # since the scheduler is constructed independently of ActionHandler.
        self.tool_scheduler = tool_scheduler
        self.code_mode_timeout_sec = code_mode_timeout_sec
        self.multi_agent_manager = multi_agent_manager
        self._active_verification_state: RunState | None = None
        self._code_mode_state: RunState | None = None

    def handle(self, action: Action, state: RunState) -> ActionOutcome:
        if self.trace is not None:
            self.trace.action_started(state, action)

        if isinstance(action, FinalAction):
            state.metrics["model_final_requests"] = state.metrics.get("model_final_requests", 0) + 1
            if self.completion_verifier is not None:
                state.metrics["verification_attempts"] = state.metrics.get("verification_attempts", 0) + 1
                self._active_verification_state = state
                try:
                    verification = self.completion_verifier.verify(
                        state,
                        self._execute_verification_action,
                    )
                finally:
                    self._active_verification_state = None
                if not verification.passed:
                    state.metrics["verification_rejected"] = (
                        state.metrics.get("verification_rejected", 0) + 1
                    )
                    observation = verification.observation or Observation(
                        kind="verification_error",
                        message=verification.reason or "Completion verification failed.",
                    )
                    state.last_observation = observation
                    if self.trace is not None:
                        self.trace.observation_created(state, observation)
                    return ActionOutcome(
                        steps=[
                            TrajectoryStep(
                                action=action,
                                observation=observation,
                                state_snapshot=state_snapshot_text(state),
                            )
                        ]
                    )
                state.metrics["verification_passed"] = state.metrics.get("verification_passed", 0) + 1
            state.metrics["memory_references_requested"] = len(action.memory)
            accepted, rejected = ground_memory_references(state, action.memory)
            state.memory_references = accepted
            state.metrics["memory_references_captured"] = len(accepted)
            state.metrics["memory_references_rejected"] = len(rejected)
            if self.trace is not None:
                for item in accepted:
                    self.trace.memory_reference_captured(state, item)
                for item in rejected:
                    self.trace.memory_reference_rejected(state, item)
            state.status = "completed"
            state.final_answer = action.answer
            return ActionOutcome(should_continue=False)

        if isinstance(action, AskAction):
            self.session.request_ask(state, action.question)
            if self.trace is not None:
                self.trace.approval_requested(state, action.question)
            return ActionOutcome(should_continue=False)

        if isinstance(action, SkillAction):
            return self._handle_skill(action, state)

        if isinstance(action, CodeModeAction):
            return self._handle_code_mode(action, state)

        if isinstance(action, DelegateAction):
            return self._handle_delegate(action, state)

        if isinstance(action, BashAction):
            return self._handle_bash(action, state)

        raise TypeError(
            f"ActionHandler.handle() only accepts BashAction/FinalAction/AskAction/SkillAction/"
            f"CodeModeAction/DelegateAction; ToolCall/ToolCallBatch are dispatched via ToolCallScheduler in "
            f"AgentLoop.run(), not through here. Got: {type(action).__name__}"
        )

    def _handle_delegate(self, action: DelegateAction, state: RunState) -> ActionOutcome:
        if self.multi_agent_manager is None:
            observation = Observation(
                kind="command_error",
                message="delegate is unavailable because no MultiAgentManager is configured.",
            )
            state.last_observation = observation
            return ActionOutcome(steps=[TrajectoryStep(action=action, observation=observation, state_snapshot=state_snapshot_text(state))])
        try:
            tasks = [
                ChildTask(
                    task_id=str(item["id"]),
                    goal=str(item["goal"]),
                    role=str(item.get("role", "worker")),
                    capability_profile=str(item.get("capability_profile", item.get("role", "worker"))),
                    provider=cast(Literal["spawn", "fork"], str(item.get("provider", "spawn"))),
                    depends_on=tuple(str(dep) for dep in item.get("depends_on", [])),
                    max_turns=int(item.get("max_turns", 0)),
                    timeout_sec=float(item.get("timeout_sec", 0)),
                    output_schema=str(item["output_schema"]) if item.get("output_schema") else None,
                )
                for item in action.tasks
            ]
            results = self.multi_agent_manager.run(
                state,
                tasks,
                parent_trajectory=getattr(state, "_active_trajectory", []),
                join=action.join,
                workflow_id=state.workflow_id,
            )
            observation = Observation(
                kind="command_result",
                exit_code=0 if all(result.status == "completed" for result in results) else 1,
                stdout_preview=json.dumps({"workflow_id": state.workflow_id, "children": [result.to_dict() for result in results]}, ensure_ascii=False),
                message="Delegated child workflow settled; use the structured results above.",
            )
        except Exception as exc:
            observation = Observation(kind="command_error", message=f"delegate rejected: {exc}")
        state.last_observation = observation
        if self.trace is not None:
            self.trace.observation_created(state, observation)
        return ActionOutcome(
            steps=[TrajectoryStep(action=action, observation=observation, state_snapshot=state_snapshot_text(state))]
        )

    def _handle_code_mode(self, action: CodeModeAction, state: RunState) -> ActionOutcome:
        run_code_mode = getattr(self.executor, "run_code_mode", None)
        if not callable(run_code_mode):
            observation = Observation(
                kind="command_error",
                message=(
                    "code_mode is only available with the Docker sandbox executor; "
                    "use read/edit/write/bash individually instead."
                ),
            )
            state.last_observation = observation
            if self.trace is not None:
                self.trace.observation_created(state, observation)
            return ActionOutcome(
                steps=[
                    TrajectoryStep(
                        action=action,
                        observation=observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                ]
            )
        if self.trace is not None:
            self.trace.sandbox_exec_started(state, f"code_mode: {len(action.script)} chars")
        self._code_mode_state = state
        try:
            observation = run_code_mode(
                action,
                state,
                timeout_sec=self.code_mode_timeout_sec,
                dispatch=self._dispatch_code_mode_call,
            )
        finally:
            self._code_mode_state = None
        state.last_observation = observation
        if self.trace is not None:
            self.trace.sandbox_exec_finished(state, observation)
            self.trace.observation_created(state, observation)
        return ActionOutcome(
            steps=[
                TrajectoryStep(
                    action=action,
                    observation=observation,
                    state_snapshot=state_snapshot_text(state),
                )
            ]
        )

    def _dispatch_code_mode_call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Route one Code Mode facade call (read/edit/write/bash) through the
        normal ToolCallScheduler dispatch path, so it gets the same policy
        evaluation, optimistic-locking, and is_error normalization as an
        ordinary model-issued tool call."""
        if self.tool_scheduler is None or self._code_mode_state is None:
            return {"is_error": True, "content": {"error": "Code Mode dispatch is unavailable: no tool scheduler configured."}}
        state = self._code_mode_state
        if tool not in {"read", "edit", "write", "bash"}:
            return {"is_error": True, "content": {"error": f"Unknown tool: {tool!r}"}}
        call = ToolCall(id=f"code-mode-{uuid4().hex[:8]}", tool=tool, arguments=arguments)  # type: ignore[arg-type]
        results = self.tool_scheduler.dispatch((call,), state)
        if not results:
            return {"is_error": True, "content": {"error": f"Code Mode dispatch produced no result for tool {tool!r}."}}
        result = results[0]
        return {"is_error": result.is_error, "content": result.content}

    def _handle_skill(self, action: SkillAction, state: RunState) -> ActionOutcome:
        loaded = self.skill_registry.load_text(action.name) if self.skill_registry else None
        if loaded is None:
            observation = Observation(
                kind="command_error",
                message=f"Skill {action.name!r} is not available in the frozen run catalog.",
            )
        else:
            observation = Observation(
                kind="command_result",
                exit_code=0,
                stdout_preview=loaded,
                message=f"Loaded skill {action.name!r} from the frozen run catalog.",
            )
            state.metrics["skill_load_actions"] = int(
                state.metrics.get("skill_load_actions", 0)
            ) + 1
            loaded_names = state.metrics.get("loaded_skill_names", [])
            if not isinstance(loaded_names, list):
                loaded_names = []
            state.metrics["loaded_skill_names"] = list(
                dict.fromkeys([*loaded_names, action.name])
            )
            if self.trace is not None:
                skill = self.skill_registry.get(action.name) if self.skill_registry else None
                self.trace.record(
                    "skill_loaded",
                    state,
                    skill_name=action.name,
                    skill_sha256=skill.sha256 if skill else None,
                    skill_source=skill.source if skill else None,
                )
        state.last_observation = observation
        if self.trace is not None:
            self.trace.observation_created(state, observation)
        return ActionOutcome(
            steps=[
                TrajectoryStep(
                    action=action,
                    observation=observation,
                    state_snapshot=state_snapshot_text(state),
                )
            ]
        )

    def _execute_verification_action(self, action: BashAction) -> Observation:
        """Execute a pre-bound verifier command through the same policy and trace boundary."""
        state = self._active_verification_state
        if state is None:
            raise RuntimeError("verification executor called outside a completion verification")
        decision = self.policy_chain.evaluate(action, state)
        if self.trace is not None:
            self.trace.policy_decision(state, decision)
        if decision.type == "deny":
            return Observation(
                kind="verification_error",
                message=f"Verifier command denied by {decision.policy_name}: {decision.reason}",
            )
        if decision.type == "require_approval":
            return Observation(
                kind="verification_error",
                message="Verifier command required approval and was not executed.",
            )
        action_to_execute = action
        if decision.type == "rewrite" and decision.rewritten_action is not None:
            action_to_execute = decision.rewritten_action
        if self.trace is not None:
            self.trace.record("verification_started", state, command=action_to_execute.command)
            self.trace.sandbox_exec_started(state, action_to_execute.command)
        execution_id = begin_execution(state, action_to_execute, self.session)
        observation = self.executor.run(action_to_execute, state)
        complete_execution(state, execution_id, observation, self.session)
        state.metrics["verification_bash_actions"] = (
            state.metrics.get("verification_bash_actions", 0) + 1
        )
        if observation.kind == "command_error":
            state.metrics["verification_command_failures"] = (
                state.metrics.get("verification_command_failures", 0) + 1
            )
        elif observation.kind == "timeout":
            state.metrics["verification_timeouts"] = (
                state.metrics.get("verification_timeouts", 0) + 1
            )
        if self.trace is not None:
            self.trace.sandbox_exec_finished(state, observation)
            self.trace.record(
                "verification_finished",
                state,
                command=action_to_execute.command,
                exit_code=observation.exit_code,
                observation_kind=observation.kind,
            )
        return observation

    def _handle_bash(self, action: BashAction, state: RunState) -> ActionOutcome:
        decision = self.policy_chain.evaluate(action, state)
        if self.trace is not None:
            self.trace.policy_decision(state, decision)
        action_to_execute = action

        if decision.type == "deny":
            state.metrics["policy_denials"] = state.metrics.get("policy_denials", 0) + 1
            observation = _policy_violation_observation(decision)
            state.last_observation = observation
            if self.trace is not None:
                self.trace.observation_created(state, observation)
            return ActionOutcome(
                steps=[
                    TrajectoryStep(
                        action=action,
                        observation=observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                ]
            )

        if decision.type == "require_approval":
            self.session.request_approval(state, action, decision)
            if self.trace is not None:
                self.trace.approval_requested(state, state.approval_question or decision.reason)
            return ActionOutcome(should_continue=False)

        if decision.type == "rewrite" and decision.rewritten_action is not None:
            action_to_execute = decision.rewritten_action

        repeated_io = _record_io_action(state, action_to_execute.command)
        if repeated_io:
            observation = Observation(
                kind="policy_violation",
                message=(
                    "repeated I/O guard: the identical read/search command was already run twice; "
                    "inspect the existing result and make the required patch or verify it instead."
                ),
            )
            state.last_observation = observation
            if self.trace is not None:
                self.trace.observation_created(state, observation)
            return ActionOutcome(
                steps=[
                    TrajectoryStep(
                        action=action_to_execute,
                        observation=observation,
                        state_snapshot=state_snapshot_text(state),
                    )
                ]
            )
        if self.trace is not None:
            self.trace.sandbox_exec_started(state, action_to_execute.command)
        execution_id = begin_execution(state, action_to_execute, self.session)
        observation = self.executor.run(action_to_execute, state)
        complete_execution(state, execution_id, observation, self.session)
        record_execution_metrics(state, observation)
        state.last_observation = observation
        if self.trace is not None:
            self.trace.sandbox_exec_finished(state, observation)
            self.trace.observation_created(state, observation)
        return ActionOutcome(
            steps=[
                TrajectoryStep(
                    action=action_to_execute,
                    observation=observation,
                    state_snapshot=state_snapshot_text(state),
                )
            ]
        )


def _policy_violation_observation(decision: PolicyDecision) -> Observation:
    return Observation(
        kind="policy_violation",
        message=f"{decision.policy_name}: {decision.reason}",
    )


def _record_io_action(state: RunState, command: str) -> bool:
    normalized = re.sub(r"[ \t\f\v]+", " ", command.strip())
    lowered = normalized.lower()
    if not normalized:
        return False

    category: str | None = None
    if re.search(r"(^|[;&|\r\n]\s*)(rg|grep|find|fd)(\.exe)?\b", lowered) or "select-string" in lowered:
        category = "search"
    elif (
        _contains_file_read_command(lowered)
        or re.search(r"(^|[;&|\r\n]\s*)(head|tail|type|get-content)(\.exe)?\b", lowered)
    ):
        category = "file_read"
    elif re.search(r"(^|[;&|\r\n]\s*)sed\s+-n\b", lowered):
        category = "file_read"
    if category is None:
        return False

    metric_name = "search_actions" if category == "search" else "file_read_actions"
    repeated_name = "repeated_searches" if category == "search" else "repeated_file_reads"
    signatures = state.metrics.setdefault("io_action_signatures", {})
    if not isinstance(signatures, dict):
        signatures = {}
        state.metrics["io_action_signatures"] = signatures
    key = f"{category}:{normalized}"
    previous = int(signatures.get(key, 0))
    signatures[key] = previous + 1
    state.metrics[metric_name] = int(state.metrics.get(metric_name, 0)) + 1
    if previous:
        state.metrics[repeated_name] = int(state.metrics.get(repeated_name, 0)) + 1
    return previous >= 2


def _contains_file_read_command(command: str) -> bool:
    cat_matches = re.finditer(
        r"(^|[;&|\r\n]\s*)(?:(?:/usr/bin/|/bin/)|command\s+)?cat(\.exe)?\b",
        command,
    )
    return any(not _is_write_only_cat(command, match.end()) for match in cat_matches)


def _is_write_only_cat(command: str, command_end: int) -> bool:
    remainder = command[command_end:].lstrip()
    if not remainder or remainder[0] in ";&|\r\n":
        return True
    if not (remainder.startswith(">") or remainder.startswith("<<")):
        return False
    command_line = remainder.splitlines()[0]
    redirect_target = r"(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)"
    without_heredoc = re.sub(
        rf"<<-?\s*{redirect_target}",
        "",
        command_line,
    )
    if re.search(r"(?<!<)<(?!<)", without_heredoc) is not None:
        return False
    without_output_redirect = re.sub(
        rf"(?:\d*)>>?\s*{redirect_target}",
        "",
        without_heredoc,
    )
    return not without_output_redirect.strip()
