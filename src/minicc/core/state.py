from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from minicc.core.protocol import Action


RunStatus = Literal["running", "waiting_approval", "completed", "failed"]
ObservationKind = Literal[
    "command_result",
    "no_output",
    "command_error",
    "timeout",
    "policy_violation",
    "protocol_error",
    "approval_result",
]


def new_run_id() -> str:
    return uuid4().hex[:12]


@dataclass
class Observation:
    kind: ObservationKind
    exit_code: int | None = None
    stdout_preview: str = ""
    stderr_preview: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    message: str = ""
    duration_ms: int = 0


@dataclass
class TrajectoryStep:
    action: Action | None
    observation: Observation


@dataclass
class RunState:
    run_id: str
    goal: str
    status: RunStatus = "running"
    workspace_host_path: Path | None = None
    container_name: str | None = None
    current_plan: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    state_summary: str = ""
    final_answer: str | None = None

    @classmethod
    def start(cls, goal: str, *, workspace_host_path: Path | None = None) -> "RunState":
        return cls(
            run_id=new_run_id(),
            goal=goal,
            workspace_host_path=workspace_host_path,
            metrics=initial_metrics(),
        )


def initial_metrics() -> dict[str, Any]:
    return {
        "turns": 0,
        "bash_actions": 0,
        "protocol_errors": 0,
        "command_failures": 0,
        "timeouts": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "latency_ms": 0,
    }
