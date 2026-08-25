"""Meta-review prompts.

Used by :class:`minicc.meta.reviewer.MetaReviewer` to diagnose reusable
harness-level improvements from immutable run evidence.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

__all__ = [
    "META_REVIEW_SYSTEM_PROMPT",
    "review_prompt",
    "schema_correction_prompt",
]

META_REVIEW_SYSTEM_PROMPT = (
    "You are miniCC's offline meta reviewer. Diagnose reusable harness-level "
    "improvements from immutable run evidence. Return exactly one JSON object."
)


def review_prompt(snapshot: Mapping[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    return f"""Review this completed miniCC run.

Focus only on reusable improvements to prompts, context, memory, policy, tools, loop control,
and verification. Do not change the run verdict. Do not propose task-specific product code.
Every finding must cite at least one evidence reference such as metrics.<field>,
trace_tail[index], run_report.<field>, or diff_preview.

Run evidence:
{payload}

Return ONLY this JSON shape:
{{
  "summary": "one concise paragraph",
  "findings": [
    {{
      "id": "F1",
      "severity": "low|medium|high",
      "area": "context|memory|policy|tools|loop|verification|other",
      "message": "reusable diagnosis",
      "evidence_refs": ["metrics.turns"]
    }}
  ],
  "suggested_changes": [
    {{
      "id": "S1",
      "finding_ids": ["F1"],
      "change": "bounded harness-level experiment",
      "expected_effect": "measurable expected outcome",
      "validation": "deterministic test or fixed A/B that would validate it"
    }}
  ]
}}
"""


def schema_correction_prompt(reason: str) -> str:
    return (
        "Your previous JSON failed validation: "
        + reason
        + ". Return the complete corrected JSON object only. Use canonical evidence paths without "
        "embedding values: state.<field>, metrics.<field>, run_report.<field>, "
        "trace_tail[<non-negative index>].<field>, or diff_preview. Every finding needs a unique F<n> "
        "id. Every suggested change needs a unique S<n> id, finding_ids, change, expected_effect, "
        "and validation. Every finding must be linked by at least one suggested change."
    )