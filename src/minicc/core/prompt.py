"""Explicit request-envelope/history/injection assembly."""

from __future__ import annotations

from typing import Any


def assemble_request(
    *,
    request: dict[str, Any],
    surface: dict[str, Any],
    injections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the only shape passed to a provider.

    System/tools/config remain envelope fields; only Surface messages become
    history.  Claimed inbox messages are appended as the final user-side input.
    """
    envelope = {
        k: request[k]
        for k in (
            "provider",
            "model",
            "reasoning_effort",
            "max_tokens",
            "temperature",
            "system",
            "tools",
            "context_window",
        )
        if k in request
    }
    return {
        "envelope": envelope,
        "history": [dict(m) for m in surface.get("messages", [])],
        "injections": [dict(m) for m in (injections or [])],
    }


def provider_messages(assembled: dict[str, Any]) -> list[dict[str, Any]]:
    envelope = assembled.get("envelope", {})
    messages = []
    if envelope.get("system") is not None:
        messages.append({"role": "system", "content": envelope["system"]})
    messages.extend(assembled.get("history", []))
    messages.extend(assembled.get("injections", []))
    return messages
