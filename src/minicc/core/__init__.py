"""Core harness primitives and the event/projection runtime."""

from .events import EventLog, EventValidationError, SessionEvent
from .projections import ProjectionRegistry, default_projections

__all__ = ["EventLog", "EventValidationError", "SessionEvent", "ProjectionRegistry", "default_projections"]
