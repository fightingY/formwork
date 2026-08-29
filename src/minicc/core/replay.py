"""Deterministic replay over the session event log."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import EventLog
from .projections import ProjectionRegistry, default_projections


@dataclass(frozen=True)
class ReplaySnapshot:
    session_id: str
    as_of_seq: int
    values: dict[str, Any]


def replay_events(
    path: Path, *, session_id: str = "replay", as_of_seq: int | None = None
) -> ReplaySnapshot:
    log = EventLog(path, session_id=session_id)
    registry = ProjectionRegistry()
    for projection in default_projections():
        registry.register(projection)
    registry.fold(
        session_id,
        log.events
        if as_of_seq is None
        else [event for event in log.events if event.seq <= as_of_seq],
    )
    snapshot = registry.snapshot(session_id)
    return ReplaySnapshot(session_id, snapshot.as_of_seq, snapshot.values)


def replay_round_trip(path: Path) -> bool:
    first = replay_events(path)
    second = replay_events(path)
    return first == second
