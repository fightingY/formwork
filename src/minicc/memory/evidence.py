"""Turn-evidence reconstruction from the EventLog (spec §6).

A background memory job must be finishable by *any* worker process, including
one that never saw the original run.  The durable anchor is the EventLog range
``[seq_start, seq_end]`` stamped when the job was enqueued; this module re-reads
those events and rebuilds the bounded evidence the L1 distiller consumes — the
same trimming discipline as the synchronous hook path, no closures involved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicc.core.events import EventLog

# Per-item text caps for the rebuilt evidence (mirrors the synchronous path).
_SHORT_ARG_CHARS = 500
_SHORT_RESULT_CHARS = 700
_MAX_EVIDENCE_EVENTS = 24


@dataclass(frozen=True)
class TurnEvidence:
    """Bounded evidence for one turn, rebuilt from its EventLog range."""

    user_message: str
    assistant_reply: str
    run_facts: str
    event_count: int = 0


def short_text(value: Any, limit: int) -> str:
    """Collapse ``value`` to one bounded single line (shared trimming rule)."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(limit - 3, 0)] + "..."


class EventEvidenceReader:
    """Reads one turn's evidence back out of an append-only events.jsonl."""

    def __init__(self, event_log_path: Path | str) -> None:
        self.event_log_path = Path(event_log_path)

    def read(
        self,
        seq_start: int,
        seq_end: int,
        *,
        run_id: str = "",
        turn: int | None = None,
    ) -> TurnEvidence:
        """Rebuild evidence for ``[seq_start, seq_end]``; never raises.

        A missing/short log (e.g. the file was rotated away) degrades to empty
        evidence — the distiller then sees the job payload text alone.
        """
        del run_id, turn  # the seq range is the authoritative filter
        if not self.event_log_path.exists():
            return TurnEvidence("", "", "", 0)
        try:
            events = [
                event
                for event in EventLog(self.event_log_path).events
                if int(seq_start) <= event.seq <= max(int(seq_start), int(seq_end))
            ]
        except Exception:  # noqa: BLE001 — evidence is best effort
            return TurnEvidence("", "", "", 0)

        user_message = ""
        assistant_reply = ""
        evidence: list[str] = []
        for event in events:
            data = event.data
            if event.type == "user/message":
                user_message = short_text(data.get("content"), 4_000) or user_message
            elif event.type == "assistant/message":
                message = data.get("message") if isinstance(data.get("message"), dict) else data
                assistant_reply = short_text(message.get("content"), 4_000) or assistant_reply
            elif event.type == "tool/call":
                evidence.append(
                    f"event#{event.seq} tool_call {data.get('tool')}: "
                    f"{short_text(data.get('arguments'), _SHORT_ARG_CHARS)}"
                )
            elif event.type == "tool/result":
                evidence.append(
                    f"event#{event.seq} tool_result {data.get('tool')} "
                    f"error={data.get('is_error')}: {short_text(data.get('content'), _SHORT_RESULT_CHARS)}"
                )
        run_facts = "Execution evidence:\n" + "\n".join(evidence[-_MAX_EVIDENCE_EVENTS:])
        return TurnEvidence(user_message, assistant_reply, run_facts, len(events))
