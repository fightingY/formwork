from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Artifact:
    id: str
    type: str
    path: str
    bytes: int
    preview: str


class ArtifactStore:
    def __init__(
        self,
        artifacts_dir: Path,
        *,
        preview_chars: int = 12_000,
        display_path_prefix: str | None = None,
    ) -> None:
        self.artifacts_dir = artifacts_dir
        self.preview_chars = preview_chars
        self.display_path_prefix = display_path_prefix
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def write_text(self, artifact_type: str, content: str) -> Artifact:
        self._seq += 1
        artifact_id = f"art_{self._seq:04d}"
        filename = f"{artifact_type}_{self._seq:04d}.txt"
        path = self.artifacts_dir / filename
        path.write_text(content, encoding="utf-8", errors="replace")
        preview = preview_text(content, self.preview_chars)
        display_path = str(path)
        if self.display_path_prefix:
            display_path = f"{self.display_path_prefix.rstrip('/')}/{filename}"
        return Artifact(
            id=artifact_id,
            type=artifact_type,
            path=display_path,
            bytes=len(content.encode("utf-8", errors="replace")),
            preview=preview,
        )


def preview_text(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    head_chars = max_chars * 3 // 5
    tail_chars = max_chars - head_chars
    return (
        content[:head_chars]
        + "\n\n... [output truncated; full content written to artifact] ...\n\n"
        + content[-tail_chars:]
    )
