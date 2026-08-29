"""Durable streaming assembler for assistant chunks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import SessionEvent


@dataclass
class StreamAssembler:
    """Fold chunk events and commit one structured assistant/message at finish."""

    turn: int
    step: int
    blocks: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    finished: bool = False

    def apply(self, chunk: dict[str, Any]) -> None:
        kind = chunk.get("type", "text-delta")
        if kind == "usage":
            self.usage = dict(chunk.get("usage") or {})
            return
        if kind == "finish":
            self.finished = True
            return
        if kind == "block-start":
            self.blocks.append(
                {
                    "type": chunk.get("blockType", "text"),
                    "text": "",
                    **({"index": chunk.get("index")} if "index" in chunk else {}),
                }
            )
            return
        if kind in {"text-delta", "reasoning-delta", "thinking-delta"}:
            typ = "reasoning" if kind != "text-delta" else "text"
            index = chunk.get("index")
            target = next(
                (
                    b
                    for b in reversed(self.blocks)
                    if (index is not None and b.get("index") == index)
                    or (index is None and b.get("type") == typ)
                ),
                None,
            )
            if target is None:
                target = {"type": typ, "text": ""}
                self.blocks.append(target)
            target["type"] = typ
            target["text"] = str(target.get("text", "")) + str(chunk.get("text", ""))
            return
        if kind == "tool-call-delta":
            self.blocks.append(
                {
                    "type": "tool-call",
                    **{k: chunk[k] for k in ("id", "name", "arguments") if k in chunk},
                }
            )
        elif kind == "block-end" and isinstance(chunk.get("block"), dict):
            idx = chunk.get("index")
            for i, b in enumerate(self.blocks):
                if idx is None or b.get("index") == idx:
                    self.blocks[i] = dict(chunk["block"])
                    break

    def accept(self, log, chunk: dict[str, Any]) -> SessionEvent:
        """Durably append a raw chunk before folding it into the assembler."""
        event = log.append(
            "assistant/chunk", {"turn": self.turn, "step": self.step, "chunk": chunk}
        )
        self.apply(chunk)
        return event

    def message(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": [{k: v for k, v in block.items() if k != "index"} for block in self.blocks],
        }

    def commit(self, log, *, request_id: str | None = None) -> SessionEvent:
        self.finished = True
        data = {"turn": self.turn, "step": self.step, "message": self.message()}
        if self.usage is not None:
            data["usage"] = self.usage
        if request_id:
            data["request_id"] = request_id
        return log.append("assistant/message", data)
