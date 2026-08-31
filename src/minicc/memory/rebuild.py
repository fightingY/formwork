"""Rebuild long-term memory projections from the canonical EventLog."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from minicc.memory.l1 import L1Distiller, MemoryStore, MemoryTurnHook


def rebuild_from_event_log(
    event_log: Any,
    store: MemoryStore,
    distiller: L1Distiller,
    *,
    project_id: str = "project",
    session_id: str = "rebuild",
    reset: bool = True,
) -> dict[str, int]:
    """Replay completed turns into fresh L1/L2/L3 read models.

    Only the derived SQLite database is reset.  EventLog remains immutable, and
    its original event sequence is copied before projection events are appended.
    """
    store.initialize()
    if reset:
        store.reset_derived()
    events = list(event_log.events)
    turns: dict[int, list[Any]] = {}
    for event in events:
        turn = event.data.get("turn")
        if isinstance(turn, int):
            turns.setdefault(turn, []).append(event)
    hook = MemoryTurnHook(store, distiller)
    processed = 0
    for turn_no, turn_events in sorted(turns.items()):
        if not any(event.type == "turn/end" for event in turn_events):
            continue
        user = ""
        assistant = ""
        for event in turn_events:
            if event.type == "user/message":
                user = str(event.data.get("content") or event.data.get("message") or user)
            elif event.type == "assistant/message":
                payload = event.data.get("message") if isinstance(event.data.get("message"), dict) else event.data
                assistant = str(payload.get("content") or assistant)
        state = SimpleNamespace(
            run_id=str(next((e.data.get("run_id") for e in turn_events if e.data.get("run_id")), f"rebuild-{turn_no}")),
            goal=user,
            final_answer=assistant,
            state_summary="",
            metrics={"turn_index": turn_no, "turn_start_seq": min(e.seq for e in turn_events)},
            status="completed",
            _event_log=event_log,
            project_id=project_id,
            workspace_host_path=project_id,
        )
        result = SimpleNamespace(state=state, user_message=user, assistant_reply=assistant, run_id=state.run_id)
        hook(session_id, result)
        processed += 1
    snapshot_id = store.publish_snapshot(project_id=project_id, generation=processed)
    return {"turns": processed, "memories": store.count_memories(), "snapshot_id": snapshot_id}


__all__ = ["rebuild_from_event_log"]
