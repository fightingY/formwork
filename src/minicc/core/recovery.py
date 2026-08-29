"""Crash recovery classification derived exclusively from event projections."""

from __future__ import annotations

from typing import Any

from .events import EventLog
from .projections import ExecutionProjection, ProjectionRegistry


def recover_session(log: EventLog, registry: ProjectionRegistry | None = None) -> dict[str, Any]:
    """Repair interrupted structural boundaries and return a recovery report."""
    repaired = log.repair_interrupted()
    reg = registry or ProjectionRegistry()
    if registry is None:
        reg.register(ExecutionProjection())
    reg.fold(log.session_id or "", log.events)
    execution = reg.value(log.session_id or "", "execution")
    return {
        "repaired_events": [e.seq for e in repaired],
        "pending_tool_calls": execution.get("pending_tool_calls", []),
        "unknown_tool_outcomes": execution.get("unknown_tool_outcomes", []),
        "allow_automatic_retry": execution.get("allow_automatic_retry", False),
    }
