"""Event-driven context compaction with locking and deterministic fallback."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from .events import EventLog
from .projections import ProjectionRegistry


class CompactionError(RuntimeError):
    pass


class CompactionManager:
    def __init__(
        self,
        log: EventLog,
        registry: ProjectionRegistry | None = None,
        *,
        threshold_ratio: float = 0.8,
        retain_ratio: float = 0.16,
        max_retries: int = 2,
        context_window: int = 128000,
    ):
        self.log = log
        self.registry = registry
        self.threshold_ratio = threshold_ratio
        self.retain_ratio = retain_ratio
        self.max_retries = max_retries
        self.context_window = context_window
        self._lock = threading.Lock()
        self._attempts = 0

    @property
    def locked(self):
        events = self.log.events
        return any(
            e.type == "compaction/start"
            and not any(
                x.type == "compaction/end"
                and x.data.get("compaction_id", x.data.get("compactionId"))
                == e.data.get("compaction_id", e.data.get("compactionId"))
                for x in events
                if x.seq > e.seq
            )
            for e in events
        )

    def compact(
        self,
        *,
        turn: int | None = None,
        summary_model: Callable[[str], str] | None = None,
        force: bool = False,
        prune: bool = False,
    ) -> dict[str, Any] | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self.locked or self._attempts >= self.max_retries:
                return None
            if prune:
                self.prune_tool_results()
                events = self.log.events
            events = self.log.events
            shadowed = {
                int(seq)
                for e in events
                if e.data.get("surfaceOp") == "replace"
                for seq in e.data.get("sourceEventSeqs", e.data.get("source_event_seqs", []))
                if str(seq).isdigit()
            }
            surface = [
                e
                for e in events
                if e.type in {"user/message", "assistant/message", "tool/result"}
                and e.seq not in shadowed
            ]
            if len(surface) < 2:
                return None
            pressure = sum(_estimate(e) for e in surface)
            threshold = int(self.context_window * self.threshold_ratio)
            if not force and pressure < threshold:
                return None
            retain = max(1, int(pressure * self.retain_ratio))
            source = surface[: -max(1, retain // max(_estimate(surface[-1]), 1))]
            # A selected region may end between a tool call and its result.
            # Move the boundary back until every selected tool result has its
            # durable call event before the compaction start.
            call_seq = {
                str(e.data.get("call_id", e.data.get("callId"))): e.seq
                for e in events
                if e.type == "tool/call"
            }
            while source and not _balanced_surface(source, events, call_seq):
                source.pop()
            if not source:
                return None
            # Do not include an open step in ordinary pressure compaction.
            if any(
                e.type == "step/start"
                and not any(
                    x.type == "step/end"
                    and x.data.get("turn") == e.data.get("turn")
                    and x.data.get("step") == e.data.get("step")
                    and x.seq > e.seq
                    for x in events
                )
                for e in events
            ):
                return None
            cid = uuid.uuid4().hex
            self.log.append(
                "compaction/start",
                {"compaction_id": cid, "turn": turn, "source_seqs": [e.seq for e in source]},
            )
            self._attempts += 1

            def event_text(event):
                value = event.data.get("content", event.data.get("message", ""))
                if isinstance(value, dict):
                    value = value.get("content", value)
                return str(value)

            text = "\n".join(event_text(e) for e in source)
            try:
                summary = summary_model(text) if summary_model else _deterministic(text)
            except Exception as exc:
                self.log.append(
                    "compaction/end",
                    {
                        "compaction_id": cid,
                        "success": False,
                        "reason": "summary_failed",
                        "error": type(exc).__name__,
                    },
                )
                self.log.append(
                    "compaction/retry",
                    {"compaction_id": cid, "reason": "summary_failed", "attempt": self._attempts},
                )
                raise CompactionError("summary model failed") from exc
            if len(summary.strip()) >= len(text.strip()):
                self.log.append(
                    "compaction/end",
                    {"compaction_id": cid, "success": False, "reason": "summary_not_smaller"},
                )
                self.log.append(
                    "compaction/retry",
                    {
                        "compaction_id": cid,
                        "reason": "summary_not_smaller",
                        "attempt": self._attempts,
                    },
                )
                raise CompactionError("summary did not shrink selected content")
            self.log.append(
                "compaction/summary",
                {"compaction_id": cid, "summary": summary, "source_seqs": [e.seq for e in source]},
            )
            first, last = surface.index(source[0]), surface.index(source[-1]) + 1
            replacement = {
                "role": "user",
                "content": summary,
                "surfaceOp": "replace",
                "sourceEventSeqs": [e.seq for e in source],
                "start": first,
                "end": last,
                "compaction_id": cid,
            }
            self.log.append("user/message", replacement)
            self.log.append(
                "compaction/end", {"compaction_id": cid, "success": True, "generation_advance": 1}
            )
            return {"compaction_id": cid, "summary": summary, "replaced": len(source)}
        finally:
            self._lock.release()

    def prune_tool_results(self, *, max_chars: int = 4000) -> int:
        count = 0
        already = {
            e.data.get("source_seq")
            for e in self.log.events
            if e.type == "compaction/prune"
        }
        for e in self.log.events:
            if (
                e.type == "tool/result"
                and e.seq not in already
                and len(str(e.data.get("content", ""))) > max_chars
            ):
                self.log.append(
                    "compaction/prune",
                    {
                        "source_seq": e.seq,
                        "preview": str(e.data.get("content", ""))[:max_chars],
                        "locator": e.data.get("locator"),
                    },
                )
                # A tool result is already durably paired and may not be
                # emitted twice. Replace only its projected surface node with
                # a bounded tool message; the original event remains intact.
                self.log.append(
                    "assistant/message",
                    {
                        "message": {
                            "role": "tool",
                            "content": str(e.data.get("content", ""))[:max_chars],
                        },
                        "call_id": e.data.get("call_id", e.data.get("callId")),
                        "locator": e.data.get("locator"),
                        "surfaceOp": "replace",
                        "sourceEventSeqs": [e.seq],
                    },
                )
                count += 1
        return count

    def recover_overflow(self, **kwargs: Any) -> bool:
        """Compact once and report whether a replacement generation advanced."""
        before = 0
        if self.registry is not None:
            try:
                before = int(
                    self.registry.value(self.log.session_id or "", "surface").get(
                        "replacement_generation", 0
                    )
                )
            except Exception:
                pass
        else:
            before = sum(
                1
                for e in self.log.events
                if e.type == "user/message" and e.data.get("surfaceOp") == "replace"
            )
        try:
            self.compact(force=True, **kwargs)
        except CompactionError:
            return False
        if self.registry is None:
            after = sum(
                1
                for e in self.log.events
                if e.type == "user/message" and e.data.get("surfaceOp") == "replace"
            )
            return after > before
        try:
            self.registry.fold(self.log.session_id or "", self.log.events)
            return (
                int(
                    self.registry.value(self.log.session_id or "", "surface").get(
                        "replacement_generation", 0
                    )
                )
                > before
            )
        except Exception:
            return False

    def recover_orphaned(self) -> int:
        """Close compaction locks left by a process crash, without replacing surface."""
        closed = 0
        events = self.log.events
        for start in events:
            if start.type != "compaction/start":
                continue
            cid = start.data.get("compaction_id", start.data.get("compactionId"))
            if any(
                e.type == "compaction/end"
                and e.data.get("compaction_id", e.data.get("compactionId")) == cid
                for e in events
                if e.seq > start.seq
            ):
                continue
            self.log.append(
                "compaction/end",
                {"compaction_id": cid, "success": False, "reason": "recovered_orphan"},
            )
            closed += 1
        return closed


def _estimate(e):
    return max(1, len(str(e.data)) * 0.25)


def _balanced_surface(source, events, call_seq) -> bool:
    """Ensure replacing a surface interval cannot strand a tool result.

    A selected result is valid only when its call belongs to the same selected
    interval, or the assistant surface node in that interval contains the call
    block.  This keeps the projected surface replayable while leaving the
    original call/result events untouched in the log.
    """
    source_seqs = {e.seq for e in source}
    first, last = min(source_seqs), max(source_seqs)
    for event in source:
        if event.type != "tool/result":
            continue
        cid = str(event.data.get("call_id") or event.data.get("callId"))
        seq = call_seq.get(cid)
        if seq is None:
            return False
        if first <= seq <= last:
            continue
        found = False
        for node in source:
            if node.type != "assistant/message":
                continue
            payload = node.data.get("message", node.data)
            content = payload.get("content") if isinstance(payload, dict) else None
            blocks = content if isinstance(content, list) else []
            if any(isinstance(block, dict) and str(block.get("id")) == cid for block in blocks):
                found = True
                break
        if not found:
            return False
    return True


def _deterministic(text):
    prefix = "[Compacted history]\n"
    body = text[: max(1, len(text) // 3)]
    result = prefix + body
    return result if len(result) < len(text) else text[: max(1, len(text) // 2)]
