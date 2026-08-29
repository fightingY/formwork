"""Durable session event log.

The event log is the sole source of session facts.  Events are intentionally
small JSON values and are appended with an fsync before projections are folded.
The implementation is synchronous because tool/model orchestration is already
performed by the synchronous runtime, and this makes crash boundaries explicit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

EVENT_TYPES: tuple[str, ...] = (
    "session/start",
    "turn/start",
    "turn/end",
    "step/start",
    "step/end",
    "user/message",
    "assistant/chunk",
    "assistant/message",
    "tool/call",
    "tool/result",
    "request/header",
    "request/context",
    "todo/write",
    "session/end-seed",
    "inbox/splice",
    "compaction/start",
    "compaction/summary",
    "compaction/end",
    "compaction/prune",
    "compaction/retry",
    "llm/retry",
    "approval/request",
    "approval/result",
    "cancel",
    "session/title",
    "permissions/update",
    "plan/update",
    "goal/update",
    "artifact/spill",
    "telemetry",
    "child/start",
    "child/result",
    "workflow/summary",
    "subagent/descriptor",
    "subagent/start",
    "subagent/end",
    "job/start",
    "job/update",
    "job/end",
    "task/claim",
    "workspace/lock",
)


class EventValidationError(ValueError):
    pass


def _json_value(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"event data is not JSON serializable: {exc}") from exc
    return value


@dataclass(frozen=True)
class SessionEvent:
    seq: int
    type: str
    data: dict[str, Any]
    time: str

    schema_version: ClassVar[int] = 2

    def __post_init__(self) -> None:
        if not isinstance(self.seq, int) or self.seq < 1:
            raise EventValidationError("seq must be a positive integer")
        if self.type not in EVENT_TYPES:
            raise EventValidationError(f"unknown event type: {self.type}")
        if not isinstance(self.data, dict):
            raise EventValidationError("event data must be an object")
        _json_value(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "data": self.data, "time": self.time}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionEvent:
        if not isinstance(value, dict):
            raise EventValidationError("event must be an object")
        return cls(
            seq=int(value["seq"]),
            type=str(value["type"]),
            data=dict(value.get("data") or {}),
            time=str(value.get("time") or ""),
        )


class EventLog:
    """Append-only JSONL event log with contiguous sequence validation."""

    _locks: dict[str, Any] = {}
    _locks_guard = __import__("threading").Lock()

    def __init__(self, path: Path, *, session_id: str | None = None) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[SessionEvent] | None = None

    def _lock(self):
        key = str(self.path.resolve())
        with self._locks_guard:
            return self._locks.setdefault(key, __import__("threading").RLock())

    def _read(self, *, repair_torn_tail: bool = True) -> list[SessionEvent]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        events: list[SessionEvent] = []
        valid_end = 0
        for line in raw.splitlines(keepends=True):
            end = valid_end + len(line)
            text = line.decode("utf-8", errors="strict").strip()
            if not text:
                valid_end = end
                continue
            try:
                event = SessionEvent.from_dict(json.loads(text))
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                KeyError,
                ValueError,
                EventValidationError,
            ):
                # A crash can leave a partial final line.  Earlier corruption is
                # never silently ignored; only the physical torn tail is repaired.
                if repair_torn_tail and end == len(raw):
                    self.path.write_bytes(raw[:valid_end])
                    break
                raise EventValidationError(f"invalid event log at byte {valid_end}") from None
            expected = len(events) + 1
            if event.seq != expected:
                raise EventValidationError(
                    f"non-contiguous seq: expected {expected}, got {event.seq}"
                )
            events.append(event)
            valid_end = end
        return events

    @property
    def events(self) -> list[SessionEvent]:
        if self._events is None:
            self._events = self._read()
        return list(self._events)

    @property
    def last_seq(self) -> int:
        return self.events[-1].seq if self.events else 0

    def append(self, event_type: str, data: dict[str, Any] | None = None) -> SessionEvent:
        if event_type not in EVENT_TYPES:
            raise EventValidationError(f"unknown event type: {event_type}")
        payload = dict(data or {})
        _json_value(payload)
        with self._lock():
            self._events = self._read()
            self._validate_transition(event_type, payload)
            event = SessionEvent(
                seq=self.last_seq + 1,
                type=event_type,
                data=payload,
                time=datetime.now(UTC).isoformat(timespec="milliseconds"),
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = (
                json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode()
            with self.path.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                dir_fd = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
            self._events.append(event)
            return event

    def _validate_transition(self, event_type: str, data: dict[str, Any]) -> None:
        events = self.events
        if event_type == "compaction/start":
            for start in events:
                start_id = start.data.get("compaction_id", start.data.get("compactionId"))
                if start.type == "compaction/start" and not any(
                    x.type == "compaction/end"
                    and x.data.get("compaction_id", x.data.get("compactionId")) == start_id
                    for x in events
                    if x.seq > start.seq
                ):
                    raise EventValidationError(
                        "compaction lock is held by an unfinished compaction"
                    )
        if event_type == "tool/result":
            call_id = (
                data.get("call_id")
                or data.get("callId")
                or data.get("tool_call_id")
                or data.get("toolCallId")
            )
            calls = [
                e
                for e in events
                if e.type == "tool/call"
                and str(e.data.get("call_id") or e.data.get("callId")) == str(call_id)
            ]
            if not call_id or not calls:
                raise EventValidationError("tool/result must pair with a prior tool/call")
            if any(
                e.type == "tool/result"
                and str(
                    e.data.get("call_id")
                    or e.data.get("callId")
                    or e.data.get("tool_call_id")
                    or e.data.get("toolCallId")
                )
                == str(call_id)
                for e in events
            ):
                raise EventValidationError("tool/result may only be recorded once per tool call")
            call = calls[-1]
            if "turn" in data and "turn" in call.data and data.get("turn") != call.data.get("turn"):
                raise EventValidationError("tool/result must remain in the tool call's turn")
            if "step" in data and "step" in call.data and data.get("step") != call.data.get("step"):
                raise EventValidationError("tool/result must remain in the tool call's step")
        if event_type == "tool/call":
            call_id = data.get("call_id") or data.get("callId")
            if not call_id:
                raise EventValidationError("tool/call requires call_id")
            if any(
                e.type == "tool/call"
                and str(e.data.get("call_id") or e.data.get("callId")) == str(call_id)
                for e in events
            ):
                raise EventValidationError("tool/call id must be unique within a session")
        if event_type == "step/end":
            starts = [
                e
                for e in events
                if e.type == "step/start"
                and e.data.get("step") == data.get("step")
                and e.data.get("turn") == data.get("turn")
            ]
            ends = [
                e
                for e in events
                if e.type == "step/end"
                and e.data.get("step") == data.get("step")
                and e.data.get("turn") == data.get("turn")
            ]
            if len(starts) <= len(ends):
                raise EventValidationError("step/end without open step")
        if event_type == "turn/end":
            starts = [
                e
                for e in events
                if e.type == "turn/start" and e.data.get("turn") == data.get("turn")
            ]
            ends = [
                e for e in events if e.type == "turn/end" and e.data.get("turn") == data.get("turn")
            ]
            if len(starts) <= len(ends):
                raise EventValidationError("turn/end without open turn")

    def append_many(self, events: list[tuple[str, dict[str, Any]]]) -> list[SessionEvent]:
        return [self.append(kind, data) for kind, data in events]

    def read_from(self, seq: int) -> list[SessionEvent]:
        return [event for event in self.events if event.seq >= seq]

    def replay(self, registry: Any, *, session_id: str | None = None, from_seq: int = 1) -> Any:
        """Fold this immutable event prefix/suffix into a ProjectionRegistry."""
        registry.fold(session_id or self.session_id or "", self.read_from(from_seq))
        return registry

    def flush(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("ab") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    def repair_interrupted(self) -> list[SessionEvent]:
        """Close open turns/steps after a cold crash, idempotently."""
        events = self.events
        open_turn: int | None = None
        open_step: tuple[int, int] | None = None
        for event in events:
            if event.type == "turn/start":
                open_turn = int(event.data.get("turn", 0))
            elif event.type == "turn/end":
                open_turn = None
            elif event.type == "step/start":
                open_step = (int(event.data.get("turn", 0)), int(event.data.get("step", 0)))
            elif event.type == "step/end":
                open_step = None
        added: list[SessionEvent] = []
        if open_step is not None:
            added.append(
                self.append(
                    "step/end",
                    {"turn": open_step[0], "step": open_step[1], "reason": {"kind": "interrupted"}},
                )
            )
        if open_turn is not None:
            added.append(
                self.append("turn/end", {"turn": open_turn, "reason": {"kind": "interrupted"}})
            )
        return added


def event_log_for(session_dir: Path, session_id: str | None = None) -> EventLog:
    return EventLog(Path(session_dir) / "events.jsonl", session_id=session_id)


# Public semantic aliases used by integrations.
SessionEventLog = EventLog
EventLogStore = EventLog
