"""Public prompt facade.

Kept for backward compatibility: re-exports the prompt catalog (``minicc.prompts``)
alongside :class:`~minicc.core.context.ContextBuilder`.
"""
from __future__ import annotations

from minicc.core.context import ContextBuilder
from minicc.prompts import (
    COMPACTION_SYSTEM_PROMPT,
    FIXED_PROBE_CONSTRAINTS,
    FIXED_PROBE_GOAL,
    HYBRID_PREFIX_SUFFIX,
    META_REVIEW_SYSTEM_PROMPT,
    MULTI_AGENT_PREFIX_SUFFIX,
    STABLE_PREFIX,
    SYSTEM_PROMPT,
    action_economy_guidance,
    child_agent_system_prompt,
    compaction_prompt,
    continuity_footer,
    io_repetition_guidance,
    review_prompt,
    schema_correction_prompt,
    state_snapshot_text,
)

__all__ = [
    "ContextBuilder",
    "STABLE_PREFIX",
    "HYBRID_PREFIX_SUFFIX",
    "MULTI_AGENT_PREFIX_SUFFIX",
    "SYSTEM_PROMPT",
    "COMPACTION_SYSTEM_PROMPT",
    "compaction_prompt",
    "META_REVIEW_SYSTEM_PROMPT",
    "review_prompt",
    "schema_correction_prompt",
    "child_agent_system_prompt",
    "FIXED_PROBE_GOAL",
    "FIXED_PROBE_CONSTRAINTS",
    "continuity_footer",
    "io_repetition_guidance",
    "action_economy_guidance",
    "state_snapshot_text",
]