from __future__ import annotations

from dataclasses import dataclass

from minicc.core.protocol import action_to_json
from minicc.core.state import Observation, RunState, TrajectoryStep


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


class ContextBuilder:
    def __init__(self, config: ContextConfig | None = None) -> None:
        self.config = config or ContextConfig()

    def build_messages(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> list[dict[str, str]]:
        recent = self.recent_trajectory(trajectory)
        dynamic_context = self._dynamic_context(state, recent)
        return [
            {"role": "system", "content": STABLE_PREFIX},
            {"role": "user", "content": "\n\n".join(dynamic_context)},
        ]

    def maybe_compact(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> None:
        if not trajectory:
            return

        compacted_steps = int(state.metrics.get("context_compacted_steps", 0))
        compactable_end = len(trajectory) - max(self.config.recent_turns, 0)
        if compactable_end <= compacted_steps:
            return

        uncompressed_trajectory = trajectory[compacted_steps:]
        estimated_messages = self._build_messages_with_trajectory(state, uncompressed_trajectory)
        if self._messages_len(estimated_messages) <= self.config.max_prompt_chars:
            return

        compactable = trajectory[compacted_steps:compactable_end]
        if not compactable:
            self._record_compaction(state, "Current recent trajectory exceeds the context budget.")
            return

        compacted_summary = _format_compaction_summary(compactable, config=self.config)
        if state.state_summary:
            state.state_summary = _trim_text(
                state.state_summary.rstrip() + "\n\n" + compacted_summary,
                self.config.summary_max_chars,
            )
        else:
            state.state_summary = compacted_summary

        state.metrics["context_compacted_steps"] = compactable_end
        self._record_compaction(
            state,
            f"Compacted {len(compactable)} older trajectory step(s) into state_summary.",
        )

    def recent_trajectory(self, trajectory: list[TrajectoryStep]) -> list[TrajectoryStep]:
        if self.config.recent_turns <= 0:
            return []
        return trajectory[-self.config.recent_turns :]

    def _dynamic_context(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> list[str]:
        dynamic_context = [
            f"Goal: {state.goal}",
            f"Run status: {state.status}",
        ]
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

    def _record_compaction(self, state: RunState, message: str) -> None:
        state.metrics["context_compactions"] = state.metrics.get("context_compactions", 0) + 1
        state.metrics["last_context_compaction"] = message

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


def _trim_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n...[truncated]...\n"
    keep = max(max_chars - len(marker), 0)
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[-tail:]
