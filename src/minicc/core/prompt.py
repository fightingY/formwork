from __future__ import annotations

from minicc.core.protocol import action_to_json
from minicc.core.state import Observation, RunState, TrajectoryStep


SYSTEM_PROMPT = """You are miniCC, a Bash-first CodeAct coding agent.

You must output exactly one JSON object per turn. Do not output Markdown.

Allowed actions:
{"type":"bash","command":"pytest -q","timeout_sec":60,"purpose":"run tests"}
{"type":"ask","question":"A concrete question for the user"}
{"type":"final","answer":"The final answer to the user"}

Rules:
- Use bash actions to inspect files, run tests, or make changes.
- Use ask only when the task is blocked by missing user input.
- Use final only when the task is complete or cannot continue.
- Treat observations as authoritative harness results.
"""


class PromptBuilder:
    def build(
        self,
        state: RunState,
        trajectory: list[TrajectoryStep],
    ) -> list[dict[str, str]]:
        dynamic_context = [
            f"Goal: {state.goal}",
            f"Run status: {state.status}",
        ]
        if state.constraints:
            dynamic_context.append("Constraints:\n" + "\n".join(f"- {item}" for item in state.constraints))
        if state.state_summary:
            dynamic_context.append(f"State summary:\n{state.state_summary}")
        if trajectory:
            dynamic_context.append("Recent trajectory:\n" + _format_trajectory(trajectory[-6:]))

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(dynamic_context)},
        ]


def _format_trajectory(steps: list[TrajectoryStep]) -> str:
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
                    f"Observation: {_format_observation(step.observation)}",
                ]
            )
        )
    return "\n\n".join(parts)


def _format_observation(observation: Observation) -> str:
    return "\n".join(
        [
            f"kind={observation.kind}",
            f"exit_code={observation.exit_code}",
            f"message={observation.message}",
            f"stdout_preview={observation.stdout_preview}",
            f"stderr_preview={observation.stderr_preview}",
        ]
    )
