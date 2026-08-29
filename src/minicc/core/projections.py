"""Rebuildable read models for the session event log.

Projection state is never authoritative: it can be dropped and folded again
from ``events.jsonl``.  The registry serializes all values at its public
boundary, preventing consumers from mutating live projection state.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .events import SessionEvent


def detached(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


class Projection(Protocol):
    key: str
    state_version: int

    def init(self) -> Any: ...
    def apply(self, state: Any, event: SessionEvent) -> Any: ...
    def view(self, state: Any) -> Any: ...


@dataclass
class ProjectionSnapshot:
    session_id: str
    as_of_seq: int
    values: dict[str, Any]


class ProjectionRegistry:
    def __init__(self) -> None:
        self._projections: dict[str, Projection] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._watermarks: dict[str, dict[str, int]] = {}
        self._listeners: list[Callable[[str, str, Any, int], None]] = []
        self._history: dict[str, list[SessionEvent]] = {}

    def register(
        self,
        projection: Projection,
        *,
        session_id: str | None = None,
        events: list[SessionEvent] | None = None,
    ) -> None:
        self._projections[projection.key] = projection
        if session_id is not None:
            self._ensure(session_id)
            self.fold(session_id, events or [], only=projection.key)

    def subscribe(self, callback: Callable[[str, str, Any, int], None]) -> None:
        self._listeners.append(callback)

    def _ensure(self, session_id: str) -> None:
        self._states.setdefault(session_id, {})
        self._watermarks.setdefault(session_id, {})
        for key, projection in self._projections.items():
            self._states[session_id].setdefault(key, projection.init())
            self._watermarks[session_id].setdefault(key, 0)

    def fold(self, session_id: str, events: list[SessionEvent], *, only: str | None = None) -> None:
        self._ensure(session_id)
        merged = {e.seq: e for e in self._history.get(session_id, [])}
        merged.update({e.seq: e for e in events})
        self._history[session_id] = [merged[k] for k in sorted(merged)]
        for event in sorted(events, key=lambda e: e.seq):
            for key, projection in self._projections.items():
                if only is not None and key != only:
                    continue
                if event.seq <= self._watermarks[session_id].get(key, 0):
                    continue
                old = detached(projection.view(self._states[session_id][key]))
                self._states[session_id][key] = projection.apply(
                    self._states[session_id][key], event
                )
                self._watermarks[session_id][key] = event.seq
                new = detached(projection.view(self._states[session_id][key]))
                if old != new:
                    for listener in self._listeners:
                        listener(session_id, key, detached(new), event.seq)

    def snapshot(self, session_id: str, *, as_of_seq: int | None = None) -> ProjectionSnapshot:
        self._ensure(session_id)
        watermark = min(self._watermarks[session_id].values(), default=0)
        if as_of_seq is None or as_of_seq >= watermark:
            values = {
                key: detached(proj.view(self._states[session_id][key]))
                for key, proj in self._projections.items()
            }
            return ProjectionSnapshot(session_id, watermark, values)
        # Historical snapshots are folded in isolation so reading as-of never
        # rewinds the live registry.
        values = {}
        for key, proj in self._projections.items():
            state = proj.init()
            for event in self._history.get(session_id, []):
                if event.seq <= as_of_seq:
                    state = proj.apply(state, event)
            values[key] = detached(proj.view(state))
        return ProjectionSnapshot(session_id, as_of_seq, values)

    def value(self, session_id: str, key: str) -> Any:
        self._ensure(session_id)
        return detached(self._projections[key].view(self._states[session_id][key]))

    def cache_rows(self, session_id: str) -> list[dict[str, Any]]:
        self._ensure(session_id)
        return [
            {
                "session_id": session_id,
                "projection_key": key,
                "state_version": self._projections[key].state_version,
                "watermark_seq": self._watermarks[session_id].get(key, 0),
                "state": detached(self._states[session_id][key]),
            }
            for key in self._projections
        ]

    def restore_cache(
        self, session_id: str, rows: list[dict[str, Any]], events: list[SessionEvent]
    ) -> None:
        self._ensure(session_id)
        for row in rows:
            key = row.get("projection_key")
            projection = self._projections.get(key)
            if projection is None or int(row.get("state_version", -1)) != projection.state_version:
                continue
            self._states[session_id][key] = detached(row.get("state", projection.init()))
            self._watermarks[session_id][key] = int(row.get("watermark_seq", 0))
        self.fold(session_id, events)


class StructuralProjection:
    key = "structural"
    state_version = 1

    def init(self) -> dict[str, Any]:
        return {
            "open_turn": None,
            "open_step": None,
            "closed_steps": [],
            "last_turn_end": None,
            "input_consumed": False,
        }

    def apply(self, s: dict[str, Any], e: SessionEvent) -> dict[str, Any]:
        d = e.data
        if e.type == "turn/start":
            s["open_turn"] = d.get("turn")
            s["input_consumed"] = False
        elif e.type == "turn/end":
            s["open_turn"] = None
            s["open_step"] = None
            s["last_turn_end"] = d.get("reason")
        elif e.type == "step/start":
            s["open_step"] = {"turn": d.get("turn"), "step": d.get("step")}
        elif e.type == "step/end":
            if s.get("open_step") is not None:
                s["closed_steps"].append(copy.deepcopy(s["open_step"]))
            s["open_step"] = None
        elif e.type == "user/message" and d.get("turn") == s.get("open_turn"):
            s["input_consumed"] = True
        return s

    def view(self, s: dict[str, Any]) -> dict[str, Any]:
        out = dict(s)
        out["has_unclosed_turn"] = out["open_turn"] is not None
        out["has_unclosed_step"] = out["open_step"] is not None
        out["compaction_boundary"] = not out["has_unclosed_turn"] and not out["has_unclosed_step"]
        return out


class SurfaceProjection:
    key = "surface"
    state_version = 2

    def init(self) -> dict[str, Any]:
        return {"nodes": [], "generation": 0, "shadowed": []}

    def apply(self, s: dict[str, Any], e: SessionEvent) -> dict[str, Any]:
        d = e.data
        if e.type in {"user/message", "assistant/message", "tool/result"}:
            op = d.get("surfaceOp")
            if op == "replace":
                source = set(
                    d.get("sourceEventSeqs", d.get("source_event_seqs", d.get("shadowed_seqs", [])))
                )
                if source:
                    indexes = [i for i, n in enumerate(s["nodes"]) if n["seq"] in source]
                    start, end = (min(indexes), max(indexes) + 1) if indexes else (0, 0)
                else:
                    start, end = int(d.get("start", 0)), int(d.get("end", 0))
                old = s["nodes"][start:end]
                s["shadowed"].extend(n["seq"] for n in old)
                s["nodes"][start:end] = [{"seq": e.seq, "type": e.type, "message": _message(d)}]
                s["generation"] += 1
            elif not d.get("shadowed"):
                s["nodes"].append({"seq": e.seq, "type": e.type, "message": _message(d)})
        return s

    def view(self, s: dict[str, Any]) -> dict[str, Any]:
        return {
            "messages": [n["message"] for n in s["nodes"]],
            "event_seqs": [n["seq"] for n in s["nodes"]],
            "nodes": copy.deepcopy(s["nodes"]),
            "replacement_generation": s["generation"],
            "shadowed": list(s["shadowed"]),
        }


def _message(d: dict[str, Any]) -> dict[str, Any]:
    if isinstance(d.get("message"), dict):
        return copy.deepcopy(d["message"])
    role = d.get("role") or ("tool" if "result" in d else "user")
    content = d.get("content", d.get("text", ""))
    return {
        "role": role,
        "content": content,
        **({"tool_call_id": d["tool_call_id"]} if d.get("tool_call_id") else {}),
    }


class RequestProjection:
    key = "request"
    state_version = 1

    def init(self) -> dict[str, Any]:
        return {}

    def apply(self, s: dict[str, Any], e: SessionEvent) -> dict[str, Any]:
        if e.type == "request/header":
            s.update(copy.deepcopy(e.data.get("header", e.data)))
            header = e.data.get("header", e.data)
            aliases = {
                "reasoningEffort": "reasoning_effort",
                "maxTokens": "max_tokens",
                "contextWindow": "context_window",
                "routeMetadata": "route_metadata",
            }
            for source, target in aliases.items():
                if source in header:
                    s[target] = copy.deepcopy(header[source])
        elif e.type == "request/context":
            s["context"] = copy.deepcopy(e.data)
        return s

    def view(self, s: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(s)


class InboxProjection:
    key = "inbox"
    state_version = 1

    def init(self) -> dict[str, Any]:
        return {"next_turn": [], "next_step": [], "claimed": [], "cancelled": []}

    def apply(self, s: dict[str, Any], e: SessionEvent) -> dict[str, Any]:
        if e.type != "inbox/splice":
            return s
        d = e.data
        queue = s["next_step"] if d.get("queue", "next-step") == "next-step" else s["next_turn"]
        op = d.get("op", "append")
        items = d.get("messages", d.get("message", []))
        items = items if isinstance(items, list) else [items]
        ids = {x.get("id") for x in queue if isinstance(x, dict)}
        if op == "append":
            queue.extend(x for x in items if isinstance(x, dict) and x.get("id") not in ids)
        elif op == "prepend":
            queue[:0] = [x for x in items if isinstance(x, dict) and x.get("id") not in ids]
        elif op == "replace":
            queue[:] = [x for x in items if isinstance(x, dict)]
        elif op == "remove":
            queue[:] = [x for x in queue if x.get("id") not in set(d.get("ids", []))]
        elif op == "clear":
            queue.clear()
        elif op == "claim":
            claim = set(d.get("ids", []))
            claimed = [x for x in queue if x.get("id") in claim]
            queue[:] = [x for x in queue if x.get("id") not in claim]
            s["claimed"].extend(claimed)
        elif op == "cancel":
            s["cancelled"].extend(d.get("ids", []))
            queue[:] = [x for x in queue if x.get("id") not in set(d.get("ids", []))]
        return s

    def view(self, s):
        return {
            "nextTurn": copy.deepcopy(s["next_turn"]),
            "nextStep": copy.deepcopy(s["next_step"]),
            "pending_message_ids": [x.get("id") for x in s["next_turn"] + s["next_step"]],
            "claimed": copy.deepcopy(s["claimed"]),
            "cancelled": list(s["cancelled"]),
        }


class ExecutionProjection:
    key = "execution"
    state_version = 1

    def init(self):
        return {
            "tool_calls": {},
            "approvals": {},
            "retries": [],
            "cancelled": False,
            "open_compaction": None,
        }

    def apply(self, s, e):
        d = e.data
        if e.type == "tool/call":
            call_id = d.get("call_id", d.get("callId"))
            s["tool_calls"][str(call_id)] = {
                "status": "started" if d.get("started") else "pending",
                **copy.deepcopy(d),
            }
        elif e.type == "tool/result":
            cid = str(
                d.get("call_id") or d.get("callId") or d.get("tool_call_id") or d.get("toolCallId")
            )
            s["tool_calls"].setdefault(cid, {}).update(
                {"status": "completed", "result": copy.deepcopy(d)}
            )
        elif e.type == "approval/request":
            s["approvals"][str(d.get("tool_call_id", d.get("toolCallId")))] = {
                "status": "pending",
                **copy.deepcopy(d),
            }
        elif e.type == "approval/result":
            s["approvals"].setdefault(str(d.get("tool_call_id", d.get("toolCallId"))), {}).update(
                {"status": d.get("status", "resolved"), **copy.deepcopy(d)}
            )
        elif e.type == "llm/retry":
            s["retries"].append(copy.deepcopy(d))
        elif e.type == "cancel":
            s["cancelled"] = True
        elif e.type == "compaction/start":
            s["open_compaction"] = copy.deepcopy(d)
        elif e.type == "compaction/end":
            s["open_compaction"] = None
        return s

    def view(self, s):
        calls = s["tool_calls"]
        return {
            **copy.deepcopy(s),
            "pending_tool_calls": [v for v in calls.values() if v.get("status") == "pending"],
            "unknown_tool_outcomes": [v for v in calls.values() if v.get("status") == "started"],
            "allow_automatic_retry": not any(v.get("status") == "started" for v in calls.values()),
        }


class TokenUsageProjection:
    key = "token_usage"
    state_version = 1

    def init(self):
        return {
            "input_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "billed_total": 0,
            "calls": {},
            "turns": {},
            "seen_steps": [],
        }

    def apply(self, s, e):
        if e.type not in {"assistant/chunk", "assistant/message"}:
            return s
        d = e.data
        usage = d.get("usage") or (
            d.get("chunk", {}).get("usage") if isinstance(d.get("chunk"), dict) else None
        )
        if not isinstance(usage, dict):
            return s
        call = str(d.get("request_id") or d.get("requestId") or f"{d.get('turn')}:{d.get('step')}")
        step_key = f"{d.get('turn')}:{d.get('step')}"
        if e.type == "assistant/message" and step_key in s["seen_steps"]:
            return s
        if call in s["calls"]:
            return s
        s["calls"][call] = copy.deepcopy(usage)
        if step_key not in s["seen_steps"]:
            s["seen_steps"].append(step_key)
        normalized = {
            "input_tokens": usage.get(
                "input_tokens",
                usage.get("inputTokens", usage.get("prompt_tokens", usage.get("promptTokens"))),
            ),
            "cache_read_tokens": usage.get(
                "cache_read_tokens",
                usage.get("cacheReadTokens", usage.get("cached_tokens", usage.get("cachedTokens"))),
            ),
            "cache_write_tokens": usage.get("cache_write_tokens", usage.get("cacheWriteTokens")),
            "output_tokens": usage.get(
                "output_tokens",
                usage.get(
                    "outputTokens", usage.get("completion_tokens", usage.get("completionTokens"))
                ),
            ),
            "reasoning_tokens": usage.get("reasoning_tokens", usage.get("reasoningTokens")),
        }
        for dst, value in normalized.items():
            if isinstance(value, (int, float)):
                s[dst] += int(value)
        turn_key = str(d.get("turn"))
        bucket = s["turns"].setdefault(turn_key, {k: 0 for k in normalized})
        for key, value in normalized.items():
            if isinstance(value, (int, float)):
                bucket[key] += int(value)
        s["billed_total"] = s["input_tokens"] + s["output_tokens"]
        return s

    def view(self, s):
        return copy.deepcopy(s)


class TranscriptProjection:
    key = "transcript"
    state_version = 1

    def init(self):
        return {"events": []}

    def apply(self, s, e):
        if e.type in {
            "user/message",
            "assistant/message",
            "tool/call",
            "tool/result",
            "approval/request",
            "approval/result",
            "llm/retry",
            "cancel",
            "compaction/start",
            "compaction/end",
        }:
            s["events"].append({"seq": e.seq, "type": e.type, "data": copy.deepcopy(e.data)})
        return s

    def view(self, s):
        return copy.deepcopy(s)


class TodoProjection:
    key = "todo"
    state_version = 1

    def init(self):
        return {"todos": []}

    def apply(self, s, e):
        if e.type == "todo/write":
            s["todos"] = copy.deepcopy(e.data.get("todos", []))
        return s

    def view(self, s):
        return copy.deepcopy(s)


class SurfaceTokenProjection:
    key = "surface_tokens"
    state_version = 1

    def init(self):
        return {"nodes": {}, "total": 0}

    def apply(self, s, e):
        if e.type in {"user/message", "assistant/message", "tool/result"}:
            chars = len(json.dumps(e.data, ensure_ascii=False))
            s["nodes"][e.seq] = max(1, (chars + 3) // 4)
            s["total"] += s["nodes"][e.seq]
            if e.data.get("surfaceOp") == "replace":
                for seq in e.data.get("shadowed_seqs", e.data.get("sourceEventSeqs", [])):
                    old = s["nodes"].pop(seq, None)
                    if old is None:
                        old = s["nodes"].pop(str(seq), 0)
                    s["total"] -= old
        return s

    def view(self, s):
        return {
            "nodes": [{"seq": str(k), "estimated_tokens": v} for k, v in s["nodes"].items()],
            "total_tokens": s["total"],
        }


class ContextPressureProjection:
    key = "context_pressure"
    state_version = 1

    def init(self):
        return {
            "pressureTokens": 0,
            "projectedTokens": 0,
            "contextWindow": None,
            "thresholdTokens": 0,
            "retainTokens": 0,
        }

    def apply(self, s, e):
        d = e.data
        if e.type == "request/header":
            h = d.get("header", d)
            s["contextWindow"] = h.get("context_window", h.get("contextWindow"))
            if s["contextWindow"]:
                s["thresholdTokens"] = int(s["contextWindow"] * 0.8)
                s["retainTokens"] = int(s["contextWindow"] * 0.16)
        if e.type in {"request/context"} and isinstance(d.get("prompt_tokens"), int):
            s["pressureTokens"] = d["prompt_tokens"]
        if e.type == "assistant/chunk":
            u = d.get("usage") or d.get("chunk", {}).get("usage", {})
            s["pressureTokens"] = int(
                u.get("prompt_tokens", u.get("input_tokens", s["pressureTokens"]))
            )
        if e.type in {"user/message", "assistant/message", "tool/result"}:
            s["projectedTokens"] = max(s["projectedTokens"], s["pressureTokens"]) + max(
                1, len(json.dumps(d, ensure_ascii=False)) // 4
            )
        else:
            s["projectedTokens"] = max(s["projectedTokens"], s["pressureTokens"])
        return s

    def view(self, s):
        out = dict(s)
        out["pressureRatio"] = (
            (s["projectedTokens"] / s["contextWindow"]) if s["contextWindow"] else 0
        )
        out["overThreshold"] = bool(
            s["thresholdTokens"] and s["projectedTokens"] >= s["thresholdTokens"]
        )
        return out


class CompactionProjection:
    key = "compaction"
    state_version = 1

    def init(self):
        return {"active": False, "current": None, "history": []}

    def apply(self, s, e):
        if e.type == "compaction/start":
            s["active"] = True
            s["current"] = {
                "id": e.data.get("compaction_id", e.data.get("compactionId")),
                "start_seq": e.seq,
                **copy.deepcopy(e.data),
            }
        elif e.type == "compaction/summary" and s["current"] is not None:
            s["current"].update(
                {
                    "summary_seq": e.seq,
                    "summary_token_count": max(1, len(str(e.data.get("summary", ""))) // 4),
                }
            )
        elif e.type == "compaction/end" and s["current"] is not None:
            s["current"].update({"end_seq": e.seq, "success": bool(e.data.get("success"))})
            s["history"].append(s["current"])
            s["active"] = False
            s["current"] = None
        return s

    def view(self, s):
        return {
            "active": s["active"],
            "current": copy.deepcopy(s["current"]),
            "history": copy.deepcopy(s["history"]),
            "orphaned": s["active"],
        }


class SessionMetadataProjection:
    key = "session_metadata"
    state_version = 1

    def init(self):
        return {
            "session_id": None,
            "project_root": None,
            "title": None,
            "format_version": 2,
            "status": "active",
        }

    def apply(self, s, e):
        if e.type == "session/start":
            s.update(
                {
                    "session_id": e.data.get("session_id"),
                    "project_root": e.data.get("project_root"),
                    "format_version": e.data.get("format_version", 2),
                }
            )
        elif e.type == "session/title":
            s["title"] = e.data.get("title")
        elif e.type == "turn/end":
            s["status"] = (
                "completed"
                if (e.data.get("reason") or {}).get("kind") == "completed"
                else "terminated"
            )
        return s

    def view(self, s):
        return copy.deepcopy(s)


class PendingInteractionProjection:
    key = "pending_interaction"
    state_version = 1

    def init(self):
        return None

    def apply(self, s, e):
        if e.type == "approval/request":
            return {"kind": "approval", "status": "pending", **copy.deepcopy(e.data)}
        if e.type == "approval/result" and s:
            return {**s, "status": e.data.get("status", "resolved")}
        if e.type == "cancel" and s:
            return {**s, "status": "cancelled"}
        return s

    def view(self, s):
        return copy.deepcopy(s)


class ArtifactSpillProjection:
    key = "artifact_spill"
    state_version = 1

    def init(self):
        return {"artifacts": {}}

    def apply(self, s, e):
        if e.type not in {"artifact/spill", "tool/result"}:
            return s
        d = e.data
        locator = d.get("locator")
        if locator:
            key = str(locator)
            s["artifacts"][key] = {
                "locator": key,
                "source_event_seq": e.seq,
                "source_call_id": d.get("call_id", d.get("callId")),
                "turn": d.get("turn"),
                "step": d.get("step"),
                "preview": d.get("preview", d.get("content", "")),
                "bytes": d.get("bytes", 0),
                "available": True,
            }
        return s

    def view(self, s):
        items = copy.deepcopy(list(s["artifacts"].values()))
        for item in items:
            item["available"] = Path(item["locator"]).is_file()
        return {"artifacts": items}


def default_projections() -> list[Projection]:
    return [
        StructuralProjection(),
        SurfaceProjection(),
        RequestProjection(),
        InboxProjection(),
        ExecutionProjection(),
        TokenUsageProjection(),
        SurfaceTokenProjection(),
        ContextPressureProjection(),
        CompactionProjection(),
        TranscriptProjection(),
        TodoProjection(),
        SessionMetadataProjection(),
        PendingInteractionProjection(),
        ArtifactSpillProjection(),
    ]
