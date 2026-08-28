"""Centralized, tunable prompt catalog for miniCC.

Every model-facing prompt lives here, organized by concern. Import from
``minicc.prompts`` (or a specific submodule) when tuning the behavior contract:

- :mod:`minicc.prompts.agent` — main CodeAct agent system prompt
- :mod:`minicc.prompts.guidance` — dynamic, RunState-dependent guidance fragments
- :mod:`minicc.prompts.compaction` — semantic-compaction prompts
- :mod:`minicc.prompts.meta_review` — offline meta-review prompts
- :mod:`minicc.prompts.cache_probe` — deterministic cache-probe goal/constraints
"""
from __future__ import annotations

from minicc.prompts.agent import STABLE_PREFIX, SYSTEM_PROMPT
from minicc.prompts.cache_probe import FIXED_PROBE_CONSTRAINTS, FIXED_PROBE_GOAL
from minicc.prompts.compaction import COMPACTION_SYSTEM_PROMPT, compaction_prompt
from minicc.prompts.guidance import (
    action_economy_guidance,
    continuity_footer,
    io_repetition_guidance,
    state_snapshot_text,
)
from minicc.prompts.meta_review import (
    META_REVIEW_SYSTEM_PROMPT,
    review_prompt,
    schema_correction_prompt,
)

__all__ = [
    "STABLE_PREFIX",
    "SYSTEM_PROMPT",
    "FIXED_PROBE_GOAL",
    "FIXED_PROBE_CONSTRAINTS",
    "COMPACTION_SYSTEM_PROMPT",
    "compaction_prompt",
    "continuity_footer",
    "io_repetition_guidance",
    "action_economy_guidance",
    "state_snapshot_text",
    "META_REVIEW_SYSTEM_PROMPT",
    "review_prompt",
    "schema_correction_prompt",
]
