from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from minicc.core.protocol import Action, action_to_dict
from minicc.core.provider import ModelUsage
from minicc.core.state import Observation, RunState
from minicc.core.tooling import ToolResult
from minicc.policy.base import PolicyDecision


@dataclass
class TraceRecorder:
    path: Path | None = None
    capture_model_responses: bool = True
    events: list[dict[str, Any]] = field(default_factory=list)
    _sequence: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.events:
            self._sequence = max(
                (int(event.get("sequence", index)) for index, event in enumerate(self.events, 1)),
                default=0,
            )
        if self.path is not None and self.path.is_file() and not self.events:
            try:
                for index, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        self._sequence = max(self._sequence, int(value.get("sequence", index)))
            except OSError:
                self._sequence = 0

    def record(self, event_type: str, state: RunState | None = None, **payload: Any) -> None:
        event: dict[str, Any] = {
            "event": event_type,
            "trace_schema_version": 1,
            "sequence": self._sequence + 1,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if state is not None:
            event["run_id"] = state.run_id
            event["status"] = state.status
        event.update(_jsonable(payload))
        self.events.append(event)
        self._sequence += 1

        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def run_started(self, state: RunState) -> None:
        self.record("run_started", state, goal=state.goal)

    def prompt_built(
        self,
        state: RunState,
        messages: list[dict[str, str]],
        *,
        prefix_profile: dict[str, Any] | None = None,
    ) -> None:
        self.record(
            "prompt_built",
            state,
            message_count=len(messages),
            prompt_chars=sum(len(message.get("content", "")) for message in messages),
            prefix_profile=prefix_profile,
        )

    def model_response(
        self,
        state: RunState,
        text: str,
        latency_ms: int,
        usage: ModelUsage | None = None,
        *,
        attempt_count: int = 1,
        retry_reasons: tuple[str, ...] = (),
    ) -> None:
        self.record(
            "model_response",
            state,
            response_preview=text[:1000],
            response_text=text if self.capture_model_responses else None,
            response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            latency_ms=latency_ms,
            attempt_count=max(int(attempt_count or 1), 1),
            retry_reasons=list(retry_reasons),
            usage=model_usage_to_dict(usage) if usage is not None else None,
            cacheability={
                "request_index": state.metrics.get("cache_prefix_request_index"),
                "prefix_epoch": state.metrics.get("cache_prefix_epoch"),
                "local_cold_start": state.metrics.get("cache_prefix_local_cold_start"),
                "previous_request_is_exact_prefix": state.metrics.get(
                    "cache_prefix_previous_is_exact"
                ),
                "prefix_reset_reason": state.metrics.get("cache_prefix_reset_reason"),
                "lcp_estimated_tokens": state.metrics.get(
                    "cache_prefix_lcp_estimated_tokens"
                ),
                "theoretical_input_tokens": state.metrics.get(
                    "cache_theoretical_input_tokens_current"
                ),
                "theoretical_output_tokens": state.metrics.get(
                    "cache_theoretical_output_tokens_current"
                ),
                "theoretical_token_kind": state.metrics.get(
                    "cache_theoretical_token_kind_current"
                ),
                "capture_efficiency_input": state.metrics.get(
                    "cache_capture_efficiency_input_current"
                ),
                "steady_state_request": bool(
                    state.metrics.get("cache_steady_state_observed")
                ),
                "steady_state_start_request_index": state.metrics.get(
                    "cache_steady_state_start_request_index"
                ),
                "steady_state_basis": state.metrics.get(
                    "cache_steady_state_basis"
                ),
                "empirical_hit_block_tokens": state.metrics.get(
                    "cache_empirical_hit_block_tokens"
                ),
                "request_metrics": (
                    state.metrics.get("cache_requests", [])[-1]
                    if state.metrics.get("cache_requests")
                    else None
                ),
            },
        )

    def llm_retry(
        self,
        state: RunState,
        *,
        route: str,
        code: str,
        retry_index: int,
        delay_ms: int,
        failure: Any,
    ) -> None:
        self.record(
            "llm/retry",
            state,
            route=route,
            code=code,
            retry_index=retry_index,
            delay_ms=delay_ms,
            failure=failure.to_dict() if hasattr(failure, "to_dict") else failure,
        )

    def failover_hop(
        self,
        state: RunState,
        *,
        from_route: str,
        to_route: str,
        code: str,
    ) -> None:
        self.record(
            "failover/hop",
            state,
            from_route=from_route,
            to_route=to_route,
            code=code,
        )

    def action_parsed(self, state: RunState, action: Action | None) -> None:
        self.record("action_parsed", state, action=action_to_dict(action) if action is not None else None)

    def action_started(self, state: RunState, action: Action) -> None:
        self.record("action_started", state, action=action_to_dict(action))

    def tool_call(
        self,
        state: RunState,
        result: ToolResult | None = None,
        *,
        call_id: str | None = None,
        tool: str | None = None,
        arguments: dict[str, Any] | None = None,
        model_order: int | None = None,
        execution_mode: str | None = None,
    ) -> None:
        if result is not None:
            call_id = result.call_id
            tool = result.tool
            model_order = result.model_order
            execution_mode = result.execution_mode
        self.record(
            "tool/call",
            state,
            schema_version=state.schema_version,
            suite_id=state.suite_id,
            milestone=state.milestone,
            stage=state.stage,
            turn=state.metrics.get("turns"),
            call_id=call_id,
            tool=tool,
            arguments=_redact_arguments(arguments or {}),
            model_order=model_order,
            execution_mode=execution_mode,
        )

    def tool_result(self, state: RunState, result: ToolResult) -> None:
        self.record(
            "tool/result",
            state,
            schema_version=state.schema_version,
            suite_id=state.suite_id,
            milestone=state.milestone,
            stage=state.stage,
            turn=state.metrics.get("turns"),
            call_id=result.call_id,
            tool=result.tool,
            model_order=result.model_order,
            execution_mode=result.execution_mode,
            is_error=result.is_error,
            content=result.content,
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
        )

    def policy_decision(self, state: RunState, decision: PolicyDecision) -> None:
        self.record(
            "policy_decision",
            state,
            decision_type=decision.type,
            reason=decision.reason,
            policy_name=decision.policy_name,
        )

    def sandbox_exec_started(self, state: RunState, command: str) -> None:
        self.record("sandbox_exec_started", state, command=command)

    def sandbox_exec_finished(self, state: RunState, observation: Observation) -> None:
        self.record("sandbox_exec_finished", state, observation=observation_to_dict(observation))

    def observation_created(self, state: RunState, observation: Observation) -> None:
        self.record("observation_created", state, observation=observation_to_dict(observation))
        for artifact_id in observation.artifact_ids:
            self.record("artifact_written", state, artifact_id=artifact_id)

    def context_compacted(
        self,
        state: RunState,
        message: str,
        *,
        strategy: str = "deterministic",
        source_steps: int = 0,
        input_chars: int = 0,
        output_chars: int = 0,
        **details: Any,
    ) -> None:
        self.record(
            "context_compacted",
            state,
            message=message,
            strategy=strategy,
            source_steps=source_steps,
            input_chars=input_chars,
            output_chars=output_chars,
            **details,
        )

    def semantic_compaction_started(
        self,
        state: RunState,
        *,
        source_steps: int,
        input_chars: int,
    ) -> None:
        self.record(
            "semantic_compaction_started",
            state,
            source_steps=source_steps,
            input_chars=input_chars,
        )

    def semantic_compaction_finished(
        self,
        state: RunState,
        *,
        source_steps: int,
        input_chars: int,
        summary_chars: int,
        usage: dict[str, Any],
    ) -> None:
        self.record(
            "semantic_compaction_finished",
            state,
            source_steps=source_steps,
            input_chars=input_chars,
            summary_chars=summary_chars,
            usage=usage,
        )

    def semantic_compaction_failed(self, state: RunState, *, error: str) -> None:
        self.record("semantic_compaction_failed", state, error=error)

    def memory_reference_captured(self, state: RunState, reference: dict[str, Any]) -> None:
        self.record("memory_reference_captured", state, reference=reference)

    def memory_reference_rejected(self, state: RunState, rejection: dict[str, Any]) -> None:
        self.record("memory_reference_rejected", state, **rejection)

    def working_memory_captured(self, state: RunState, path: Path, item_count: int) -> None:
        self.record("working_memory_captured", state, path=str(path), item_count=item_count)

    def working_memory_injected(
        self,
        state: RunState,
        *,
        source_run_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        self.record(
            "working_memory_injected",
            state,
            source_run_id=source_run_id,
            item_count=len(items),
            references=[
                {
                    "path": item.get("path"),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                }
                for item in items
            ],
        )

    def approval_requested(self, state: RunState, question: str) -> None:
        self.record("approval_requested", state, question=question)

    def approval_resolved(self, state: RunState, status: str, reason: str = "") -> None:
        self.record("approval_resolved", state, approval_status=status, reason=reason)

    def checkpoint_created(self, state: RunState, checkpoint_id: str, reason: str) -> None:
        self.record("checkpoint_created", state, checkpoint_id=checkpoint_id, reason=reason)

    def checkpoint_restored(self, state: RunState, checkpoint_id: str) -> None:
        self.record("checkpoint_restored", state, checkpoint_id=checkpoint_id)

    def run_resumed(self, state: RunState, trajectory_steps: int) -> None:
        self.record("run_resumed", state, trajectory_steps=trajectory_steps, resume_count=state.resume_count)

    def run_interrupted(self, state: RunState, trajectory_steps: int) -> None:
        self.record("run_interrupted", state, trajectory_steps=trajectory_steps)

    def run_completed(self, state: RunState) -> None:
        self.record("run_completed", state, final_answer=state.final_answer)

    def run_failed(self, state: RunState) -> None:
        self.record("run_failed", state, state_summary=state.state_summary)


def trace_path_for(state: RunState) -> Path | None:
    if state.run_dir is None:
        return None
    return state.run_dir / "trace.jsonl"


def observation_to_dict(observation: Observation) -> dict[str, Any]:
    return {
        "kind": observation.kind,
        "exit_code": observation.exit_code,
        "message": observation.message,
        "stdout_preview": observation.stdout_preview,
        "stderr_preview": observation.stderr_preview,
        "artifact_ids": observation.artifact_ids,
        "duration_ms": observation.duration_ms,
    }


def model_usage_to_dict(usage: ModelUsage) -> dict[str, Any]:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_tokens": usage.cached_tokens,
        "cache_hit_tokens": usage.cache_hit_tokens,
        "cache_miss_tokens": usage.cache_miss_tokens,
        "cache_hit_rate": usage.cache_hit_rate,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    sensitive = {"api_key", "authorization", "password", "secret", "token"}
    redacted: dict[str, Any] = {}
    for key, value in arguments.items():
        if any(marker in key.lower() for marker in sensitive):
            redacted[key] = "<redacted>"
        elif isinstance(value, dict):
            redacted[key] = _redact_arguments(value)
        else:
            redacted[key] = value
    return redacted
