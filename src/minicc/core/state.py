from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from minicc.core.ledger import LEDGER_SCHEMA_VERSION
from minicc.core.protocol import Action, BashAction, action_to_dict, parse_action

RunStatus = Literal[
    "running",
    "waiting_approval",
    "interrupted",
    "orphaned",
    "completed",
    "failed",
]
ObservationKind = Literal[
    "command_result",
    "no_output",
    "command_error",
    "timeout",
    "policy_violation",
    "protocol_error",
    "approval_result",
    "verification_error",
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
    state_snapshot: str = ""


@dataclass
class RunState:
    run_id: str
    goal: str
    prompt_namespace: str = ""
    schema_version: int = LEDGER_SCHEMA_VERSION
    suite_id: str | None = None
    milestone: str = ""
    stage: str = "daily_development"
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
    checkpoint_sequence: int = 0
    latest_checkpoint_id: str | None = None
    resume_count: int = 0
    execution_journal: list[dict[str, Any]] = field(default_factory=list)
    memory_references: list[dict[str, Any]] = field(default_factory=list)
    working_memory: list[dict[str, Any]] = field(default_factory=list)
    working_memory_source_run_id: str | None = None
    repository_profile: dict[str, Any] = field(default_factory=dict)
    project_guide: dict[str, Any] = field(default_factory=dict)
    root_run_id: str | None = None
    parent_run_id: str | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    role: str = "root"
    capability_profile: str = "root"
    depth: int = 0
    lease_epoch: int | None = None
    # V5 session transport: prior-turn conversation rows (["role","content"])
    # injected into context.  NOT persisted to state.json -- transcript.jsonl is
    # the single source of truth for conversation history (plan §5.1).  Populated
    # by SessionEngine from SessionStore.history_messages before each turn runs.
    session_history: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        goal: str,
        *,
        workspace_host_path: Path | None = None,
        run_dir: Path | None = None,
        artifacts_dir: Path | None = None,
        suite_id: str | None = None,
        milestone: str = "",
        stage: str = "daily_development",
        prompt_namespace: str = "",
    ) -> RunState:
        return cls(
            run_id=new_run_id(),
            goal=goal,
            prompt_namespace=prompt_namespace,
            workspace_host_path=workspace_host_path,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            suite_id=suite_id,
            milestone=milestone,
            stage=stage,
            metrics=initial_metrics(),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Conversation history lives only in transcript.jsonl (V5 §5.1); the run's
        # own state.json must not carry a second copy of it.
        data.pop("session_history", None)
        for key in ["run_dir", "artifacts_dir", "workspace_host_path"]:
            if data[key] is not None:
                data[key] = str(data[key])
        if self.pending_action is not None:
            data["pending_action"] = action_to_dict(self.pending_action)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
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
            prompt_namespace=str(data.get("prompt_namespace") or ""),
            schema_version=int(data.get("schema_version", 1)),
            suite_id=data.get("suite_id"),
            milestone=str(data.get("milestone") or ""),
            stage=str(data.get("stage") or "daily_development"),
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
            checkpoint_sequence=int(data.get("checkpoint_sequence", 0)),
            latest_checkpoint_id=data.get("latest_checkpoint_id"),
            resume_count=int(data.get("resume_count", 0)),
            execution_journal=list(data.get("execution_journal", [])),
            memory_references=list(data.get("memory_references", [])),
            working_memory=list(data.get("working_memory", [])),
            working_memory_source_run_id=data.get("working_memory_source_run_id"),
            repository_profile=dict(data.get("repository_profile", {})),
            project_guide=dict(data.get("project_guide", {})),
            root_run_id=data.get("root_run_id"),
            parent_run_id=data.get("parent_run_id"),
            workflow_id=data.get("workflow_id"),
            task_id=data.get("task_id"),
            role=str(data.get("role", "root")),
            capability_profile=str(data.get("capability_profile", "root")),
            depth=int(data.get("depth", 0)),
            lease_epoch=data.get("lease_epoch"),
        )


def initial_metrics() -> dict[str, Any]:
    return {
        "started_at": None,
        "completed_at": None,
        "total_duration_ms": 0,
        "turns": 0,
        "bash_actions": 0,
        "tool_call_steps": 0,
        "tool_calls": 0,
        "read_tool_calls": 0,
        "edit_tool_calls": 0,
        "write_tool_calls": 0,
        "bash_tool_calls": 0,
        "profile": "baseline-bash",
        "root_turns": 0,
        "child_runs_started": 0,
        "child_runs_completed": 0,
        "child_runs_failed": 0,
        "child_runs_cancelled": 0,
        "workflow_count": 0,
        "workflow_depth": 0,
        "max_concurrent_children": 0,
        "delegate_steps": 0,
        "parallel_read_groups": 0,
        "actual_parallelism": 0,
        "write_lease_acquire": 0,
        "write_lease_denial": 0,
        "write_lease_release": 0,
        "capability_denials": 0,
        "readonly_bash_denials": 0,
        "workspace_mutation_violations": 0,
        "review_iterations": 0,
        "verifier_attempts": 0,
        "verifier_passes": 0,
        "trace_events": 0,
        "transcript_records": 0,
        "max_parallel_tool_calls": 4,
        "max_tool_calls_per_step": 16,
        "protocol_errors": 0,
        "provider_errors": 0,
        "provider_request_attempts": 0,
        "provider_retried_requests": 0,
        "provider_name": "",
        "provider_response_models": [],
        "provider_system_fingerprints": [],
        "infrastructure_errors": 0,
        "command_failures": 0,
        "timeouts": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "cache_metrics_available": False,
        "cache_metric_requests": 0,
        "cache_unreported_requests": 0,
        "cache_observed_hit_tokens": 0,
        "cache_observed_prompt_tokens": 0,
        "cache_hit_rate": None,
        "latency_ms": 0,
        "artifact_bytes": 0,
        "policy_denials": 0,
        "approvals_requested": 0,
        "context_compactions": 0,
        "context_compacted_steps": 0,
        "context_compaction_strategy": "deterministic",
        "context_compaction_input_chars": 0,
        "context_compaction_output_chars": 0,
        "context_compaction_chars_saved": 0,
        "context_compaction_events": [],
        "context_budget_overflows": 0,
        "context_budget_triggered": False,
        "context_retention_expected": 0,
        "context_retention_retained": 0,
        "context_retention_rate": None,
        "context_retention_markers": [],
        "semantic_compaction_requests": 0,
        "semantic_compaction_successes": 0,
        "semantic_compaction_failures": 0,
        "semantic_compaction_prompt_tokens": 0,
        "semantic_compaction_completion_tokens": 0,
        "semantic_compaction_total_tokens": 0,
        "semantic_compaction_cached_tokens": 0,
        "semantic_compaction_latency_ms": 0,
        "prompt_char_samples": 0,
        "prompt_chars_total": 0,
        "prompt_chars_max": 0,
        "prompt_chars_mean": 0.0,
        "prompt_layout": "rebuild",
        "stable_prefix_hash": "",
        "stable_prefix_chars": 0,
        "stable_prefix_estimated_tokens": 0,
        "stable_prefix_message_count": 0,
        "stable_prefix_profile": {},
        "cache_prefix_epoch": 0,
        "cache_prefix_request_index": 0,
        "cache_prefix_cold_start_requests": 0,
        "cache_prefix_exact_append_requests": 0,
        "cache_prefix_reset_requests": 0,
        "cache_prefix_reset_reasons": {},
        "cache_prefix_local_cold_start": True,
        "cache_prefix_previous_is_exact": False,
        "cache_prefix_lcp_message_count": 0,
        "cache_prefix_lcp_chars": 0,
        "cache_prefix_lcp_estimated_tokens": 0,
        "cache_prefix_current_estimated_tokens": 0,
        "cache_prefix_reset_reason": "",
        "cache_prefix_request_sha256": "",
        "cache_theoretical_input_tokens": 0,
        "cache_theoretical_output_tokens": 0,
        "cache_capture_observed_hit_tokens": 0,
        "cache_capture_efficiency_input": None,
        "cache_capture_efficiency_output": None,
        "cache_steady_state_hit_tokens": 0,
        "cache_steady_state_prompt_tokens": 0,
        "cache_steady_state_hit_rate": None,
        "cache_steady_state_observed": False,
        "cache_steady_state_start_request_index": None,
        "cache_steady_state_request_count": 0,
        "cache_steady_state_basis": "first_observed_cache_hit",
        "cache_empirical_hit_block_tokens": 0,
        "cache_requests": [],
        "file_read_actions": 0,
        "search_actions": 0,
        "repeated_file_reads": 0,
        "repeated_searches": 0,
        "memory_references_requested": 0,
        "memory_references_captured": 0,
        "memory_references_rejected": 0,
        "working_memory_candidates": 0,
        "working_memory_items_injected": 0,
        "working_memory_invalid_adoptions": 0,
        "working_memory_injection_events": 0,
        "memory_distill_failed": 0,
        "memory_distill_successes": 0,
        "memory_distill_count": 0,
        "memory_stored": 0,
        "memory_dedup_failed": 0,
        "memory_dedup_store": 0,
        "memory_dedup_skip": 0,
        "memory_dedup_update": 0,
        "memory_dedup_merge": 0,
        "memory_recall_failed": 0,
        "l1_memories_recalled": 0,
        "l1_memories_injected": 0,
        "persona_synthesized": 0,
        "persona_synthesis_failed": 0,
        "persona_signal_count": 0,
        "l3_persona_injected": 0,
        "scenario_synthesized": 0,
        "scenario_synthesis_failed": 0,
        "scenario_count": 0,
        "l2_scenarios_injected": 0,
        "skill_load_actions": 0,
        "loaded_skill_names": [],
        "checkpoints_created": 0,
        "resumes_completed": 0,
        "resume_drift_errors": 0,
        "model_final_requests": 0,
        "verification_attempts": 0,
        "verification_rejected": 0,
        "verification_passed": 0,
        "verification_bash_actions": 0,
        "verification_command_failures": 0,
        "verification_timeouts": 0,
    }


def trajectory_step_to_dict(step: TrajectoryStep) -> dict[str, Any]:
    return {
        "action": action_to_dict(step.action) if step.action is not None else None,
        "observation": asdict(step.observation),
        "state_snapshot": step.state_snapshot,
    }


def trajectory_step_from_dict(data: dict[str, Any]) -> TrajectoryStep:
    raw_action = data.get("action")
    action = parse_action(json.dumps(raw_action)) if isinstance(raw_action, dict) else None
    raw_observation = data.get("observation")
    if not isinstance(raw_observation, dict):
        raise ValueError("Checkpoint trajectory step is missing an observation.")
    return TrajectoryStep(
        action=action,
        observation=Observation(**raw_observation),
        state_snapshot=str(data.get("state_snapshot") or ""),
    )


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
