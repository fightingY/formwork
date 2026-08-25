"""Fixed goal/constraints for the deterministic cache probe.

Used by :mod:`minicc.evals.cache_probe_runner` to build a transport-level cache
probe whose prompt is stable enough to verify with SHA-256 anchors.
"""
from __future__ import annotations

__all__ = ["FIXED_PROBE_GOAL", "FIXED_PROBE_CONSTRAINTS"]

FIXED_PROBE_GOAL = (
    "Inspect the supplied repository evidence and choose the next minimal verification action. "
    "This is a deterministic transport-level cache probe; rely only on the supplied observations."
)
FIXED_PROBE_CONSTRAINTS = (
    "Return exactly one Bash-first JSON action.",
    "Do not assume that a displayed command was actually executed outside this fixed sequence.",
)