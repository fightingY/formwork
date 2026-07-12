from __future__ import annotations

import json
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from minicc.core.protocol import Action, BashAction, action_to_dict, parse_action


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
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid4().hex[:8]}"


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
    run_dir: Path | None = None
    artifacts_dir: Path | None = None
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
    pending_action: BashAction | None = None
    approval_question: str | None = None
    last_observation: Observation | None = None

    @classmethod
    def start(
        cls,
        goal: str,
        *,
        workspace_host_path: Path | None = None,
        run_dir: Path | None = None,
        artifacts_dir: Path | None = None,
    ) -> "RunState":
        return cls(
            run_id=new_run_id(),
            goal=goal,
            workspace_host_path=workspace_host_path,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            metrics=initial_metrics(),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ["run_dir", "artifacts_dir", "workspace_host_path"]:
            if data[key] is not None:
                data[key] = str(data[key])
        if self.pending_action is not None:
            data["pending_action"] = action_to_dict(self.pending_action)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunState":
        pending_action = data.get("pending_action")
        if pending_action is not None:
            pending_action = parse_action(json.dumps(pending_action))
            if not isinstance(pending_action, BashAction):
                pending_action = None

        last_observation_data = data.get("last_observation")
        last_observation = Observation(**last_observation_data) if isinstance(last_observation_data, dict) else None

        return cls(
            run_id=str(data["run_id"]),
            goal=str(data["goal"]),
            status=data.get("status", "running"),
            run_dir=_path_or_none(data.get("run_dir")),
            artifacts_dir=_path_or_none(data.get("artifacts_dir")),
            workspace_host_path=_path_or_none(data.get("workspace_host_path")),
            container_name=data.get("container_name"),
            current_plan=list(data.get("current_plan", [])),
            constraints=list(data.get("constraints", [])),
            open_questions=list(data.get("open_questions", [])),
            approvals=list(data.get("approvals", [])),
            artifacts=list(data.get("artifacts", [])),
            metrics=dict(data.get("metrics", initial_metrics())),
            state_summary=str(data.get("state_summary", "")),
            final_answer=data.get("final_answer"),
            pending_action=pending_action,
            approval_question=data.get("approval_question"),
            last_observation=last_observation,
        )


def initial_metrics() -> dict[str, Any]:
    return {
        "started_at": None,
        "completed_at": None,
        "total_duration_ms": 0,
        "turns": 0,
        "bash_actions": 0,
        "protocol_errors": 0,
        "provider_errors": 0,
        "command_failures": 0,
        "timeouts": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "cache_hit_rate": 0,
        "latency_ms": 0,
        "artifact_bytes": 0,
        "policy_denials": 0,
        "approvals_requested": 0,
        "context_compactions": 0,
        "context_compacted_steps": 0,
    }


def save_run_state(state: RunState, path: Path | None = None) -> Path:
    target = path or _state_path_for(state)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_run_state(path: Path) -> RunState:
    return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def state_path_for_run(run_id: str, *, runs_root: Path | None = None) -> Path:
    root = runs_root or Path.cwd() / ".minicc" / "runs"
    return root / run_id / "state.json"


def _state_path_for(state: RunState) -> Path:
    if state.run_dir is not None:
        return state.run_dir / "state.json"
    return state_path_for_run(state.run_id)


def _path_or_none(value: Any) -> Path | None:
    if value is None:
        return None
    return Path(str(value))
