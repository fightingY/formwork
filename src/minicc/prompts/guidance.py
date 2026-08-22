"""Dynamic guidance fragments injected into the agent context.

These differ from :mod:`minicc.prompts.agent` in that each is a function of the
current :class:`~minicc.core.state.RunState`, produced at build time.
"""
from __future__ import annotations

from minicc.core.state import RunState

__all__ = [
    "continuity_footer",
    "io_repetition_guidance",
    "action_economy_guidance",
    "state_snapshot_text",
]


def continuity_footer(state: RunState) -> str:
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


def io_repetition_guidance(state: RunState) -> str:
    repeated_reads = int(state.metrics.get("repeated_file_reads", 0) or 0)
    repeated_searches = int(state.metrics.get("repeated_searches", 0) or 0)
    if not repeated_reads and not repeated_searches:
        return ""
    return (
        "I/O repetition guard: the same file/search action has already been repeated "
        f"({repeated_reads} file read(s), {repeated_searches} search(es)). "
        "Do not repeat it again; make the smallest required patch or run the authoritative verification now."
    )


def action_economy_guidance(state: RunState) -> str:
    file_reads = int(state.metrics.get("file_read_actions", 0) or 0)
    if file_reads < 1:
        return ""
    if not any("authoritative offline verification commands" in item for item in state.constraints):
        return ""
    return (
        "Action economy: if the root cause and smallest change are clear from the current "
        "evidence, the next bash action should apply that change and run one authoritative "
        "verification command in the same shell command when policy permits. Do not inspect a "
        "test solely to reconfirm behavior already established unambiguously by the goal and "
        "source; authoritative post-change verification is sufficient. Otherwise inspect only "
        "the specific missing evidence."
    )


def state_snapshot_text(state: RunState) -> str:
    """Freeze mutable guidance at an immutable trajectory boundary."""

    parts: list[str] = []
    repetition_guidance = io_repetition_guidance(state)
    if repetition_guidance:
        parts.append(repetition_guidance)
    action_economy = action_economy_guidance(state)
    if action_economy:
        parts.append(action_economy)
    if state.open_questions:
        parts.append("Open questions:\n" + "\n".join(f"- {item}" for item in state.open_questions))
    if state.approval_question:
        parts.append(f"Pending approval question:\n{state.approval_question}")
    if state.status != "running":
        parts.append(f"Run status: {state.status}")
    return "\n\n".join(parts)