"""Child-agent prompt for the V4 multi-agent profile.

Used by :func:`minicc.multi_agent._complete_child_model` when a subprocess child
is driven by the configured child model.
"""
from __future__ import annotations

__all__ = ["child_agent_system_prompt"]


def child_agent_system_prompt(role: str) -> str:
    return (
        "You are a child coding agent in miniCC. Return one concise JSON object with "
        "summary, findings, and evidence. Do not claim hidden reasoning. "
        f"Your role is {role}; obey its capabilities and do not edit files."
    )