"""Rebuild long-term memory projections from the canonical EventLog (spec §10).

Two modes share one contract — the EventLog is the immutable L0 source and the
rebuild only ever rewrites the derived SQLite read model:

- ``semantic``: replay each completed turn through the L1 distiller (LLM calls)
  to re-extract memories, then escalate and republish the snapshot.
- ``deterministic``: no model calls at all — repair the existing read model in
  place (backfill ``topic_key``, rebuild the FTS5 index, optionally re-embed
  NULL vectors) and republish the snapshot.  ``reset`` does not apply here.

In both modes the projection shim carries **no event sink**, so lifecycle
events (``memory/l1_extracted`` & co.) are never re-appended to the EventLog —
a rebuild is observable in its manifest, not in L0.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

from minicc.memory.l1 import (
    Embedder,
    L1Distiller,
    MemoryStore,
    MemoryTurnHook,
)

REBUILD_MODES: tuple[str, ...] = ("deterministic", "semantic")


def _layer_counts(store: MemoryStore) -> dict[str, int]:
    with sqlite3.connect(str(store.db_path)) as conn:
        l2 = int(
            conn.execute("SELECT COUNT(*) FROM scenarios WHERE status='active'").fetchone()[0]
        )
        l3_candidate = int(
            conn.execute("SELECT COUNT(*) FROM persona WHERE state='candidate'").fetchone()[0]
        )
        l3_confirmed = int(
            conn.execute("SELECT COUNT(*) FROM persona WHERE state='confirmed'").fetchone()[0]
        )
    return {
        "l2_count": l2,
        "l3_candidate_count": l3_candidate,
        "l3_confirmed_count": l3_confirmed,
    }


def _completed_turns(events: list[Any]) -> dict[int, list[Any]]:
    turns: dict[int, list[Any]] = {}
    for event in events:
        turn = event.data.get("turn")
        if isinstance(turn, int):
            turns.setdefault(turn, []).append(event)
    return {
        turn_no: turn_events
        for turn_no, turn_events in turns.items()
        if any(event.type == "turn/end" for event in turn_events)
    }


def rebuild_from_event_log(
    event_log: Any,
    store: MemoryStore,
    distiller: L1Distiller | None = None,
    *,
    project_id: str = "project",
    session_id: str = "rebuild",
    reset: bool = True,
    mode: str = "semantic",
    embedder: Embedder | None = None,
    deduper: Any = None,
    escalator: Any = None,
) -> dict[str, Any]:
    """Replay the EventLog into the L1/L2/L3 read models and return a manifest."""
    if mode not in REBUILD_MODES:
        raise ValueError(f"unknown rebuild mode: {mode} (expected one of {REBUILD_MODES})")
    store.initialize()
    events = list(event_log.events)
    completed = _completed_turns(events)

    if mode == "deterministic":
        # Repair, not recreate: the L1 rows stay, only their derived indexes are
        # rebuilt (topic_key backfill, FTS5, optional embeddings) — no LLM.
        repair = store.repair_derived(embedder=embedder)
        snapshot_id = store.publish_snapshot(project_id=project_id, generation=0)
        manifest: dict[str, Any] = {
            "mode": mode,
            "event_log": str(getattr(event_log, "path", "")),
            "event_count": len(events),
            "turn_count": len(completed),
            "l1_count": store.count_memories(),
            "snapshot_id": snapshot_id,
            "reset": False,
            "repair": repair,
        }
        manifest.update(_layer_counts(store))
        return manifest

    if distiller is None:
        raise ValueError("semantic rebuild requires a distiller")
    if reset:
        store.reset_derived()
    hook = MemoryTurnHook(store, distiller, deduper=deduper, escalator=escalator)
    processed = 0
    for turn_no, turn_events in sorted(completed.items()):
        user = ""
        assistant = ""
        for event in turn_events:
            if event.type == "user/message":
                user = str(event.data.get("content") or event.data.get("message") or user)
            elif event.type == "assistant/message":
                payload = (
                    event.data.get("message")
                    if isinstance(event.data.get("message"), dict)
                    else event.data
                )
                assistant = str(payload.get("content") or assistant)
        state = SimpleNamespace(
            run_id=str(
                next(
                    (
                        e.data.get("run_id")
                        for e in turn_events
                        if e.data.get("run_id")
                    ),
                    f"rebuild-{turn_no}",
                )
            ),
            goal=user,
            final_answer=assistant,
            state_summary="",
            metrics={"turn_index": turn_no, "turn_start_seq": min(e.seq for e in turn_events)},
            status="completed",
            # No event sink: a rebuild must never append memory/... events to L0.
            _event_log=None,
            project_id=project_id,
            workspace_host_path=project_id,
        )
        result = SimpleNamespace(
            state=state, user_message=user, assistant_reply=assistant, run_id=state.run_id
        )
        hook(session_id, result)
        processed += 1
    snapshot_id = store.publish_snapshot(project_id=project_id, generation=processed)
    manifest = {
        "mode": mode,
        "event_log": str(getattr(event_log, "path", "")),
        "event_count": len(events),
        "turn_count": processed,
        "l1_count": store.count_memories(),
        "snapshot_id": snapshot_id,
        "reset": bool(reset),
    }
    manifest.update(_layer_counts(store))
    return manifest


__all__ = ["REBUILD_MODES", "rebuild_from_event_log"]
