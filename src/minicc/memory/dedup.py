"""L1 dedup: LLM batch conflict detection (store / skip / update / merge).

V5.1 memory redesign (``docs/V5_1_MEMORY_REDESIGN_PLAN.md``) P3.  After
distillation, each freshly-distilled L1 memory is compared against the existing
candidate pool via one ``json_mode`` call that classifies it into one of four
actions (§2: store / skip / update / merge, aligned with 腾讯 ``l1-dedup``):

- **store**  — brand new, insert as a new row;
- **skip**   — exact duplicate, drop it;
- **update** — supersedes an existing row, replace its content;
- **merge**  — combines with an existing row, replace its content with the merge.

On any failure (:class:`L1Deduper.dedup` returns ``None``) the caller appends
everything unchanged — 宁可冗余，不可丢 (plan §4.5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from minicc.core.provider import CompletionOptions, ModelProvider, ProviderError
from minicc.memory.l1 import L1Memory

DedupAction = Literal["store", "skip", "update", "merge"]
DEDUP_ACTIONS: frozenset[str] = frozenset({"store", "skip", "update", "merge"})

_DEDUP_SYSTEM = (
    "You deduplicate coding-agent memories. Return ONLY a JSON array, no prose."
)


@dataclass(frozen=True)
class DedupDecision:
    """One per-new-memory classification (``index`` is the position in the batch)."""

    index: int
    action: DedupAction
    record_id: int | None = None
    content: str | None = None


class L1Deduper:
    """Batch conflict detection; never raises.

    ``dedup`` returns ``None`` on provider/parse failure (so the caller falls
    back to append-all), or a list of :class:`DedupDecision` (possibly empty
    when the model declined to classify, which also means append-all for safety).
    """

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def dedup(
        self,
        new_memories: list[L1Memory],
        candidates: list[L1Memory],
    ) -> list[DedupDecision] | None:
        if not new_memories:
            return []
        messages = [
            {"role": "system", "content": _DEDUP_SYSTEM},
            {"role": "user", "content": _dedup_prompt(new_memories, candidates)},
        ]
        try:
            response = self.provider.complete(
                messages,
                options=CompletionOptions(json_mode=True, max_tokens=None),
            )
        except (ProviderError, RuntimeError):
            return None
        return self._parse(response.text)

    def _parse(self, text: str) -> list[DedupDecision] | None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, list):
            return None
        decisions: list[DedupDecision] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            action = item.get("action")
            if not isinstance(index, int) or action not in DEDUP_ACTIONS:
                continue
            record_id = item.get("record_id")
            content = item.get("content")
            decisions.append(
                DedupDecision(
                    index=index,
                    action=action,
                    record_id=record_id if isinstance(record_id, int) else None,
                    content=content if isinstance(content, str) and content.strip() else None,
                )
            )
        return decisions


def _dedup_prompt(new_memories: list[L1Memory], candidates: list[L1Memory]) -> str:
    lines = [
        "Compare each NEW memory against the EXISTING memories and classify it.",
        "Actions:",
        '  "store"  — brand new, no existing match (record_id: null, content: null)',
        '  "skip"   — exact duplicate of an existing memory (give its record_id)',
        '  "update" — supersedes an existing memory; give record_id + the new content',
        '  "merge"  — combines with an existing memory; give record_id + the merged content',
        "Return ONLY a JSON array, one object per NEW memory:",
        '  [{"index": <int>, "action": "store"|"skip"|"update"|"merge",',
        '    "record_id": <int|null>, "content": "<string|null>"}]',
        "",
        "EXISTING memories:",
    ]
    if candidates:
        for candidate in candidates:
            record_id = candidate.record_id if candidate.record_id is not None else "?"
            lines.append(f"- [{record_id}] ({candidate.type}) {candidate.content}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("NEW memories:")
    for index, memory in enumerate(new_memories):
        lines.append(f"- [index {index}] ({memory.type}) {memory.content}")
    return "\n".join(lines)