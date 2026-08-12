from __future__ import annotations


def build_manifest(version: str, files: list[str]) -> dict[str, object]:
    """Build a release manifest."""
    return {"id": version, "files": files}
