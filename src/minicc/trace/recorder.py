from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minicc.core.protocol import Action, action_to_dict
from minicc.core.provider import ModelUsage
from minicc.core.state import Observation, RunState
from minicc.policy.base import PolicyDecision


@dataclass
class TraceRecorder:
    path: Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event_type: str, state: RunState | None = None, **payload: Any) -> None:
        event: dict[str, Any] = {
            "event": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if state is not None:
            event["run_id"] = state.run_id
            event["status"] = state.status
        event.update(_jsonable(payload))
        self.events.append(event)

        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def run_started(self, state: RunState) -> None:
        self.record("run_started", state, goal=state.goal)

    def prompt_built(self, state: RunState, messages: list[dict[str, str]]) -> None:
        self.record(
            "prompt_built",
            state,
            message_count=len(messages),
            prompt_chars=sum(len(message.get("content", "")) for message in messages),
        )

    def model_response(
        self,
        state: RunState,
        text: str,
        latency_ms: int,
        usage: ModelUsage | None = None,
    ) -> None:
        self.record(
            "model_response",
            state,
            response_preview=text[:1000],
            latency_ms=latency_ms,
            usage=model_usage_to_dict(usage) if usage is not None else None,
        )

    def action_parsed(self, state: RunState, action: Action | None) -> None:
        self.record("action_parsed", state, action=action_to_dict(action) if action is not None else None)

    def action_started(self, state: RunState, action: Action) -> None:
        self.record("action_started", state, action=action_to_dict(action))

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

    def context_compacted(self, state: RunState, message: str) -> None:
        self.record("context_compacted", state, message=message)

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
