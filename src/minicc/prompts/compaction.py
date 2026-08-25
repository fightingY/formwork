"""Semantic-compaction prompts.

Used by :class:`minicc.memory.compaction.SemanticCompactor` to distill older
trajectory into durable working context.
"""
from __future__ import annotations

__all__ = ["COMPACTION_SYSTEM_PROMPT", "compaction_prompt"]

COMPACTION_SYSTEM_PROMPT = (
    "You compact coding-agent execution history into durable working context. "
    "Return exactly one JSON object and do not invent facts."
)


def compaction_prompt(
    trajectory_text: str,
    *,
    existing_summary: str,
    retention_markers: tuple[str, ...],
    max_summary_chars: int,
) -> str:
    existing = existing_summary.strip() or "(none)"
    markers = "\n".join(f"- {marker}" for marker in retention_markers) or "- (none)"
    return f"""Distill the older coding-agent trajectory for future coding turns.

Preserve only grounded, actionable information:
- key files and repository facts
- root cause and failed hypotheses
- decisions and their reasons
- patch state and verification evidence
- artifact pointers
- open work and risks

The run is still active when this prompt is generated. Never summarize unfinished work as
"no open work"; preserve the next concrete action or explicitly state that the goal remains
unverified. Avoid recommending repeated reads of files whose contents are already known.

Every retention marker below must appear verbatim in the summary when it is supported by the input or
existing summary. Do not claim an unsupported marker as a fact.

Retention markers:
{markers}

Existing compact summary:
{existing}

Trajectory to compact:
{trajectory_text}

Return ONLY JSON with this shape:
{{"summary": "concise Markdown, at most {max_summary_chars} characters"}}
"""