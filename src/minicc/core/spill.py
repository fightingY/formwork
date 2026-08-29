"""Execution-layer spill contract for oversized tool output."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SpillResult:
    preview: str
    locator: str
    bytes: int
    available: bool = True


class SpillStore:
    def __init__(self, root: Path, *, preview_chars: int = 4000):
        self.root = Path(root)
        self.preview_chars = preview_chars

    def write_stream(self, stream, name: str) -> SpillResult:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        total = 0
        preview = []
        remaining = self.preview_chars
        with path.open("wb") as out:
            for chunk in iter(lambda: stream.read(65536), b""):
                if isinstance(chunk, str):
                    chunk = chunk.encode()
                out.write(chunk)
                total += len(chunk)
                if remaining > 0:
                    text = chunk.decode("utf-8", errors="replace")
                    preview.append(text[:remaining])
                    remaining -= len(text[:remaining])
            out.flush()
            os.fsync(out.fileno())
        return SpillResult("".join(preview), str(path), total)

    def write(self, content: str, name: str) -> SpillResult:
        from io import BytesIO

        return self.write_stream(BytesIO(content.encode("utf-8")), name)

    def read(self, locator: str) -> bytes:
        return Path(locator).read_bytes()
