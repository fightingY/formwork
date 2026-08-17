from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_GUIDE_NAME = "MINICC.md"
PROJECT_GUIDE_MAX_CHARS = 20_000
PROFILE_SCHEMA_VERSION = 1

_IGNORED_DIRS = {
    ".git",
    ".minicc",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}


@dataclass(frozen=True)
class ProjectGuide:
    path: str
    sha256: str
    text: str
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "chars": len(self.text),
            "truncated": self.truncated,
            "text": self.text,
        }


@dataclass(frozen=True)
class RepositoryProfile:
    schema_version: int
    workspace_kind: str
    root_entries: tuple[str, ...]
    build_files: tuple[str, ...]
    test_files: tuple[str, ...]
    source_directories: tuple[str, ...]
    candidate_test_commands: tuple[str, ...]
    guide: ProjectGuide | None = None
    guide_status: str = "absent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_kind": self.workspace_kind,
            "root_entries": list(self.root_entries),
            "build_files": list(self.build_files),
            "test_files": list(self.test_files),
            "source_directories": list(self.source_directories),
            "candidate_test_commands": list(self.candidate_test_commands),
            "guide": self.guide.to_dict() if self.guide is not None else None,
            "guide_status": self.guide_status,
        }

    def context_text(self) -> str:
        payload = {
            "workspace_kind": self.workspace_kind,
            "build_files": list(self.build_files),
            "test_files": list(self.test_files),
            "source_directories": list(self.source_directories),
            "candidate_test_commands": list(self.candidate_test_commands),
            "guide_status": self.guide_status,
        }
        return "Repository profile (deterministic, read-only):\n" + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )


def inspect_repository(workspace: Path) -> RepositoryProfile:
    """Build a bounded repository profile without executing project commands."""
    root_entries: list[str] = []
    build_files: list[str] = []
    test_files: list[str] = []
    source_directories: list[str] = []

    for entry in sorted(workspace.iterdir(), key=lambda item: item.name.lower()):
        root_entries.append(entry.name)
        if entry.is_file() and entry.name in {
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "gradlew",
            "gradlew.bat",
            "mvnw",
            "mvnw.cmd",
            "pyproject.toml",
            "setup.cfg",
            "package.json",
            "Makefile",
        }:
            build_files.append(entry.name)
        if entry.is_dir() and entry.name not in _IGNORED_DIRS:
            if entry.name in {"src", "app", "lib", "tests", "test"}:
                source_directories.append(entry.name)

    for relative in _bounded_files(workspace):
        name = relative.name.lower()
        if name.startswith("test_") or name.endswith("_test.py") or "/test/" in f"/{relative.as_posix()}/":
            test_files.append(relative.as_posix())
        elif name.endswith("test.java") or name.endswith("tests.java"):
            test_files.append(relative.as_posix())

    workspace_kind = _workspace_kind(build_files)
    commands = _candidate_test_commands(workspace_kind, build_files, test_files)
    guide, guide_status = load_project_guide(workspace)
    return RepositoryProfile(
        schema_version=PROFILE_SCHEMA_VERSION,
        workspace_kind=workspace_kind,
        root_entries=tuple(root_entries),
        build_files=tuple(build_files),
        test_files=tuple(test_files[:50]),
        source_directories=tuple(source_directories),
        candidate_test_commands=tuple(commands),
        guide=guide,
        guide_status=guide_status,
    )


def load_project_guide(workspace: Path) -> tuple[ProjectGuide | None, str]:
    path = workspace / PROJECT_GUIDE_NAME
    if not path.exists():
        return None, "absent"
    if path.is_symlink():
        return None, "symlink_rejected"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    truncated = len(text) > PROJECT_GUIDE_MAX_CHARS
    return (
        ProjectGuide(
            path=PROJECT_GUIDE_NAME,
            sha256=digest,
            text=text[:PROJECT_GUIDE_MAX_CHARS],
            truncated=truncated,
        ),
        "loaded_truncated" if truncated else "loaded",
    )


def write_repository_profile(profile: RepositoryProfile, output_path: Path) -> str:
    payload = profile.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_files(workspace: Path, *, limit: int = 500) -> list[Path]:
    files: list[Path] = []
    for path in sorted(workspace.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not path.is_file() or any(part in _IGNORED_DIRS for part in path.relative_to(workspace).parts):
            continue
        files.append(path.relative_to(workspace))
        if len(files) >= limit:
            break
    return files


def _workspace_kind(build_files: list[str]) -> str:
    if "pom.xml" in build_files:
        return "maven"
    if "build.gradle" in build_files or "build.gradle.kts" in build_files:
        return "gradle"
    if "pyproject.toml" in build_files or "setup.cfg" in build_files:
        return "python"
    if "package.json" in build_files:
        return "node"
    return "unknown"


def _candidate_test_commands(kind: str, build_files: list[str], test_files: list[str]) -> list[str]:
    del test_files
    if kind == "maven":
        return ["./mvnw test", "mvn test"] if "mvnw" in build_files else ["mvn test"]
    if kind == "gradle":
        return ["./gradlew test", "gradle test"]
    if kind == "python":
        return ["python -m pytest", "python -m unittest discover -s tests"]
    if kind == "node":
        return ["npm test"]
    return []
