from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from minicc.core.protocol import action_to_json
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.memory.compaction import CompactionError, ContextCompactor
from minicc.memory.feedback import FeedbackMemory
from minicc.skills.registry import SkillRegistry
from minicc.trace.recorder import TraceRecorder


STABLE_PREFIX = """You are miniCC, a Bash-first CodeAct coding agent.

You must output exactly one JSON object per turn. Do not output Markdown.

Allowed actions:
{"type":"bash","command":"pytest -q","timeout_sec":60,"purpose":"run tests"}
{"type":"ask","question":"A concrete question for the user"}
{"type":"final","answer":"The final answer to the user"}

Behavior rules:
- Use bash actions to inspect files, run tests, or make changes.
- Use ask only when the task is blocked by missing user input.
- Use final only when the task is complete or cannot continue.
- Treat observations as authoritative harness results.

Sandbox and policy constraints:
- Bash commands run inside the configured miniCC execution environment.
- Commands may be denied, rewritten, or paused for approval before execution.
- Network, destructive filesystem, sensitive path, timeout, and action budget policies may apply.
- If a command is denied, choose a safer alternative or ask the user.

Observation contract:
- command_result means the command ran successfully and produced output.
- no_output means the command exited successfully without stdout or stderr.
- command_error means the command ran and exited non-zero.
- timeout means the command exceeded its allowed runtime.
- policy_violation means the harness blocked the action before execution.
- protocol_error means the previous model output violated the JSON action protocol.
- approval_result means the user approved, denied, or answered a pending request.
"""


@dataclass(frozen=True)
class ContextConfig:
    max_prompt_chars: int = 120_000
    recent_turns: int = 6
    artifact_preview_chars: int = 12_000
    summary_max_chars: int = 12_000
    field_preview_chars: int = 4_000
    compaction_strategy: Literal["disabled", "deterministic", "semantic"] = "deterministic"
    retention_markers: tuple[str, ...] = ()


class ContextBuilder:
    def __init__(
        self,
        config: ContextConfig | None = None,
        *,
        skill_registry: SkillRegistry | None = None,
        feedback_memory: FeedbackMemory | None = None,
        trace: TraceRecorder | None = None,
        semantic_compactor: ContextCompactor | None = None,
    ) -> None:
        self.config = config or ContextConfig()
        if self.config.compaction_strategy not in {"disabled", "deterministic", "semantic"}:
            raise ValueError("compaction_strategy must be disabled, deterministic, or semantic")
        if self.config.compaction_strategy == "semantic" and semantic_compactor is None:
            raise ValueError("semantic compaction requires a semantic_compactor")
        self.skill_registry = skill_registry
        self.feedback_memory = feedback_memory
        self.trace = trace
        self.semantic_compactor = semantic_compactor

    def build_messages(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> list[dict[str, str]]:
        state.metrics["context_compaction_strategy"] = self.config.compaction_strategy
        recent = self.recent_trajectory(trajectory)
        dynamic_context = self._dynamic_context(state, recent)
        messages = [
            {"role": "system", "content": STABLE_PREFIX},
            {"role": "user", "content": "\n\n".join(dynamic_context)},
        ]
        self._record_prompt_metrics(state, messages)
        if self.trace is not None:
            self.trace.prompt_built(state, messages)
        return messages

    def maybe_compact(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> None:
        if not trajectory:
            return

        state.metrics["context_compaction_strategy"] = self.config.compaction_strategy
        if self.config.compaction_strategy == "disabled":
            estimated_messages = self._build_messages_with_trajectory(state, trajectory)
            if self._messages_len(estimated_messages) > self.config.max_prompt_chars:
                state.metrics["context_budget_triggered"] = True
                state.metrics["context_budget_overflows"] = (
                    state.metrics.get("context_budget_overflows", 0) + 1
                )
                artifact_markers = [
                    artifact_id
                    for step in trajectory
                    for artifact_id in step.observation.artifact_ids
                ]
                markers = tuple(dict.fromkeys([*self.config.retention_markers, *artifact_markers]))
                state.metrics["context_retention_markers"] = list(markers)
                full_context = self.format_trajectory(trajectory)
                state.metrics["context_retention_expected"] = len(markers)
                state.metrics["context_retention_retained"] = sum(
                    marker in full_context for marker in markers
                )
                state.metrics["context_retention_rate"] = (
                    state.metrics["context_retention_retained"] / len(markers) if markers else None
                )
            return

        compacted_steps = int(state.metrics.get("context_compacted_steps", 0))
        compactable_end = len(trajectory) - max(self.config.recent_turns, 0)
        if compactable_end <= compacted_steps:
            return

        uncompressed_trajectory = trajectory[compacted_steps:]
        estimated_messages = self._build_messages_with_trajectory(state, uncompressed_trajectory)
        if self._messages_len(estimated_messages) <= self.config.max_prompt_chars:
            return
        state.metrics["context_budget_triggered"] = True

        compactable = trajectory[compacted_steps:compactable_end]
        if not compactable:
            state.metrics["context_budget_overflows"] = state.metrics.get("context_budget_overflows", 0) + 1
            return

        trajectory_text = self.format_trajectory(compactable)
        event_markers = tuple(
            dict.fromkeys(
                [
                    *self.config.retention_markers,
                    *(
                        artifact_id
                        for step in compactable
                        for artifact_id in step.observation.artifact_ids
                    ),
                ]
            )
        )
        known_markers = state.metrics.get("context_retention_markers", [])
        if not isinstance(known_markers, list):
            known_markers = []
        state.metrics["context_retention_markers"] = list(dict.fromkeys([*known_markers, *event_markers]))
        strategy = self.config.compaction_strategy
        if strategy == "semantic":
            compacted_summary = self._semantic_summary(
                state,
                trajectory_text,
                len(compactable),
                event_markers,
            )
        else:
            deterministic = _format_compaction_summary(compactable, config=self.config)
            compacted_summary = _append_summary(state.state_summary, deterministic)

        source_text = _append_summary(state.state_summary, trajectory_text)
        state.state_summary = _preserve_retention_markers(
            compacted_summary,
            source_text=source_text,
            markers=tuple(state.metrics["context_retention_markers"]),
            max_chars=self.config.summary_max_chars,
        )

        state.metrics["context_compacted_steps"] = compactable_end
        state.metrics["context_compaction_strategy"] = strategy
        state.metrics["context_compaction_input_chars"] = (
            state.metrics.get("context_compaction_input_chars", 0) + len(trajectory_text)
        )
        state.metrics["context_compaction_output_chars"] = (
            state.metrics.get("context_compaction_output_chars", 0) + len(state.state_summary)
        )
        state.metrics["context_compaction_chars_saved"] = (
            state.metrics.get("context_compaction_chars_saved", 0)
            + max(len(trajectory_text) - len(state.state_summary), 0)
        )
        active_context = _append_summary(
            state.state_summary,
            self.format_trajectory(trajectory[compactable_end:]),
        )
        self._record_retention_metrics(state, active_context=active_context)
        self._record_compaction(
            state,
            f"Compacted {len(compactable)} older trajectory step(s) into state_summary.",
            strategy=strategy,
            source_steps=len(compactable),
            input_chars=len(trajectory_text),
            output_chars=len(state.state_summary),
        )

    def recent_trajectory(self, trajectory: list[TrajectoryStep]) -> list[TrajectoryStep]:
        if self.config.compaction_strategy == "disabled":
            return trajectory
        if self.config.recent_turns <= 0:
            return []
        return trajectory[-self.config.recent_turns :]

    def _dynamic_context(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> list[str]:
        dynamic_context: list[str] = []
        if self.skill_registry is not None:
            skill_catalog = self.skill_registry.catalog_text()
            if skill_catalog:
                dynamic_context.append(skill_catalog)
        if self.feedback_memory is not None:
            memory_context = self.feedback_memory.context_text(state.goal)
            if memory_context:
                dynamic_context.append(memory_context)

        dynamic_context.extend(
            [
                f"Goal: {state.goal}",
                f"Run status: {state.status}",
            ]
        )
        budget_guidance = _budget_guidance(state)
        if budget_guidance:
            dynamic_context.append(budget_guidance)
        repeated_reads = int(state.metrics.get("repeated_file_reads", 0) or 0)
        repeated_searches = int(state.metrics.get("repeated_searches", 0) or 0)
        if repeated_reads or repeated_searches:
            dynamic_context.append(
                "I/O repetition guard: the same file/search action has already been repeated "
                f"({repeated_reads} file read(s), {repeated_searches} search(es)). "
                "Do not repeat it again; make the smallest required patch or run the authoritative verification now."
            )
        if state.constraints:
            dynamic_context.append("Constraints:\n" + "\n".join(f"- {item}" for item in state.constraints))
        if state.state_summary:
            dynamic_context.append(f"State summary:\n{state.state_summary}")
        if state.open_questions:
            dynamic_context.append("Open questions:\n" + "\n".join(f"- {item}" for item in state.open_questions))
        if state.approval_question:
            dynamic_context.append(f"Pending approval question:\n{state.approval_question}")
        if state.last_observation is not None:
            dynamic_context.append(f"Last observation:\n{self.format_observation(state.last_observation)}")
        if trajectory:
            dynamic_context.append("Recent trajectory:\n" + self.format_trajectory(trajectory))
        return dynamic_context

    def _build_messages_with_trajectory(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": STABLE_PREFIX},
            {"role": "user", "content": "\n\n".join(self._dynamic_context(state, trajectory))},
        ]

    def format_trajectory(self, steps: list[TrajectoryStep]) -> str:
        parts: list[str] = []
        for index, step in enumerate(steps, start=1):
            if step.action is None:
                action_text = "<protocol_error>"
            else:
                action_text = action_to_json(step.action)
            parts.append(
                "\n".join(
                    [
                        f"Step {index}",
                        f"Action: {action_text}",
                        f"Observation: {self.format_observation(step.observation)}",
                    ]
                )
            )
        return "\n\n".join(parts)

    def format_observation(self, observation: Observation) -> str:
        lines = [
            f"kind={observation.kind}",
            f"exit_code={observation.exit_code}",
            f"message={_trim_text(observation.message, self.config.field_preview_chars)}",
        ]
        if observation.stdout_preview:
            lines.append(
                f"stdout_preview={_trim_text(observation.stdout_preview, self.config.artifact_preview_chars)}"
            )
        else:
            lines.append("stdout_preview=")
        if observation.stderr_preview:
            lines.append(
                f"stderr_preview={_trim_text(observation.stderr_preview, self.config.artifact_preview_chars)}"
            )
        else:
            lines.append("stderr_preview=")
        if observation.artifact_ids:
            lines.append("artifact_ids=" + ", ".join(observation.artifact_ids))
        return "\n".join(lines)

    def _semantic_summary(
        self,
        state: RunState,
        trajectory_text: str,
        source_steps: int,
        retention_markers: tuple[str, ...],
    ) -> str:
        assert self.semantic_compactor is not None
        try:
            result = self.semantic_compactor.compact(
                state,
                trajectory_text=trajectory_text,
                existing_summary=state.state_summary,
                retention_markers=retention_markers,
                source_steps=source_steps,
            )
        except CompactionError as exc:
            state.metrics["semantic_compaction_failures"] = (
                state.metrics.get("semantic_compaction_failures", 0) + 1
            )
            state.metrics["last_semantic_compaction_error"] = str(exc)
            deterministic = _format_compaction_summary_from_text(
                trajectory_text,
                max_chars=self.config.summary_max_chars,
            )
            return _append_summary(state.state_summary, deterministic)
        state.metrics["semantic_compaction_successes"] = (
            state.metrics.get("semantic_compaction_successes", 0) + 1
        )
        # The model summary is useful but must not be allowed to erase the
        # authoritative fact that compaction happened while the run is still
        # active.  Without this footer a terse summary can incorrectly report
        # "no open work", causing the next turn to repeat inspection forever.
        return _append_summary(result.summary, _continuity_footer(state))

    def _record_compaction(
        self,
        state: RunState,
        message: str,
        *,
        strategy: str,
        source_steps: int,
        input_chars: int,
        output_chars: int,
    ) -> None:
        state.metrics["context_compactions"] = state.metrics.get("context_compactions", 0) + 1
        state.metrics["last_context_compaction"] = message
        if self.trace is not None:
            self.trace.context_compacted(
                state,
                message,
                strategy=strategy,
                source_steps=source_steps,
                input_chars=input_chars,
                output_chars=output_chars,
            )

    def _record_prompt_metrics(self, state: RunState, messages: list[dict[str, str]]) -> None:
        prompt_chars = self._messages_len(messages)
        samples = int(state.metrics.get("prompt_char_samples", 0)) + 1
        total = int(state.metrics.get("prompt_chars_total", 0)) + prompt_chars
        state.metrics["prompt_char_samples"] = samples
        state.metrics["prompt_chars_total"] = total
        state.metrics["prompt_chars_max"] = max(int(state.metrics.get("prompt_chars_max", 0)), prompt_chars)
        state.metrics["prompt_chars_mean"] = total / samples

    def _record_retention_metrics(self, state: RunState, *, active_context: str) -> None:
        raw_markers = state.metrics.get("context_retention_markers", self.config.retention_markers)
        markers = tuple(str(marker) for marker in raw_markers)
        retained = sum(marker in active_context for marker in markers)
        state.metrics["context_retention_expected"] = len(markers)
        state.metrics["context_retention_retained"] = retained
        state.metrics["context_retention_rate"] = retained / len(markers) if markers else None

    @staticmethod
    def _messages_len(messages: list[dict[str, str]]) -> int:
        return sum(len(item.get("content", "")) for item in messages)


SYSTEM_PROMPT = STABLE_PREFIX


def _format_compaction_summary(steps: list[TrajectoryStep], *, config: ContextConfig) -> str:
    lines = ["Compacted trajectory summary:"]
    for index, step in enumerate(steps, start=1):
        action_text = "<protocol_error>" if step.action is None else action_to_json(step.action)
        observation = step.observation
        lines.extend(
            [
                f"- Step {index}: action={_trim_text(action_text, 600)}",
                (
                    "  observation="
                    f"kind={observation.kind}; "
                    f"exit_code={observation.exit_code}; "
                    f"message={_trim_text(observation.message, 600)}"
                ),
            ]
        )
        if observation.artifact_ids:
            lines.append("  artifacts=" + ", ".join(observation.artifact_ids))
    return _trim_text("\n".join(lines), config.summary_max_chars)


def _format_compaction_summary_from_text(text: str, *, max_chars: int) -> str:
    return _trim_text("Compacted trajectory summary:\n" + text, max_chars)


def _append_summary(existing: str, addition: str) -> str:
    if not existing.strip():
        return addition.strip()
    if not addition.strip():
        return existing.strip()
    return existing.rstrip() + "\n\n" + addition.strip()


def _preserve_retention_markers(
    summary: str,
    *,
    source_text: str,
    markers: tuple[str, ...],
    max_chars: int,
) -> str:
    supported = [marker for marker in markers if marker in source_text]
    missing = [marker for marker in supported if marker not in summary]
    footer = ""
    if missing:
        footer = "\n\nRetention markers:\n" + "\n".join(f"- {marker}" for marker in missing)
    if not footer:
        return _trim_text(summary, max_chars)
    if 0 < max_chars < len(footer):
        return _trim_text(summary, max_chars)
    body_budget = max(max_chars - len(footer), 0)
    return _trim_text(summary, body_budget).rstrip() + footer


def _continuity_footer(state: RunState) -> str:
    """Keep unfinished-run semantics explicit across semantic compaction."""

    lines = [
        "Execution continuity (authoritative):",
        f"- Goal: {state.goal}",
        f"- Run status: {state.status}; the goal is not complete while this run is active.",
        "- Continue from the last observation and take the next necessary action; do not treat a missing patch as completion.",
    ]
    if state.current_plan:
        lines.append("- Current plan: " + " | ".join(str(item) for item in state.current_plan))
    if state.open_questions:
        lines.append("- Open questions: " + " | ".join(str(item) for item in state.open_questions))
    return "\n".join(lines)


def _trim_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n...[truncated]...\n"
    if max_chars <= len(marker):
        return text[:max_chars]
    keep = max(max_chars - len(marker), 0)
    head = keep // 2
    tail = keep - head
    tail_text = text[-tail:] if tail else ""
    return text[:head] + marker + tail_text


def _budget_guidance(state: RunState) -> str:
    max_turns = state.metrics.get("max_turns")
    turns = state.metrics.get("turns", 0)
    if not isinstance(max_turns, int) or max_turns <= 0:
        return ""
    ratio = turns / max_turns
    remaining = max(max_turns - turns, 0)
    if ratio >= 0.8:
        return (
            f"Budget status: {remaining} model turn(s) remain. Stop exploring. "
            "Run only the minimum verification still needed, then return final immediately."
        )
    if ratio >= 0.6:
        return (
            f"Budget status: {remaining} model turn(s) remain. Converge now: "
            "finish the smallest correct change, verify once, and avoid repeated inspection."
        )
    return ""
