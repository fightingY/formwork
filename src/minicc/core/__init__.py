"""Core harness primitives and the event/projection runtime."""

from .events import EventLog, EventValidationError, SessionEvent
from .multi_agent import (
    ChildResult,
    ChildTask,
    Evidence,
    Fact,
    ForkSnapshot,
    JobRecord,
    JobRegistry,
    MultiAgentError,
    MultiAgentManager,
    WorkspaceLease,
    WorkspaceLeaseRegistry,
)
from .projections import ProjectionRegistry, default_projections

__all__ = [
    "EventLog", "EventValidationError", "SessionEvent", "ProjectionRegistry", "default_projections",
    "ChildResult", "ChildTask", "Evidence", "Fact", "ForkSnapshot", "JobRecord", "JobRegistry",
    "MultiAgentError", "MultiAgentManager", "WorkspaceLease", "WorkspaceLeaseRegistry",
]
