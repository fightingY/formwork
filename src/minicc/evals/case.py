from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from minicc.sandbox.workspace import workspace_content_digest


@dataclass(frozen=True)
class EvalCase:
    name: str
    prompt: str
    case_dir: Path
    fixture_dir: Path
    sandbox_mode: str = "locked"
    capability: str = ""
    proves: str = ""
    budget: dict[str, Any] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    writable_paths: tuple[str, ...] | None = None
    context: dict[str, Any] = field(default_factory=dict)
    completion_gate: bool = False
    definition_sha256: str = ""


def discover_cases(path: Path) -> list[EvalCase]:
    if path.is_file():
        return [load_case(path)]

    case_files = sorted(path.rglob("case.yaml"))
    if not case_files:
        return []
    return [load_case(case_file) for case_file in case_files]


def load_case(path: Path) -> EvalCase:
    source = path.read_bytes()
    data = yaml.safe_load(source.decode("utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Eval case must be a YAML mapping: {path}")

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"Eval case missing prompt: {path}")

    case_dir = path.parent
    fixture_value = data.get("fixture", "fixture")
    fixture_dir = (case_dir / str(fixture_value)).resolve()
    if not fixture_dir.exists():
        raise ValueError(f"Eval fixture does not exist: {fixture_dir}")

    assertions = data.get("assertions", [])
    if not isinstance(assertions, list):
        raise ValueError(f"Eval assertions must be a list: {path}")

    budget = data.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}

    context = data.get("context", {})
    if not isinstance(context, dict):
        raise ValueError(f"context must be a mapping: {path}")
    retention_markers = context.get("retention_markers", [])
    if not isinstance(retention_markers, list) or not all(
        isinstance(item, str) and item.strip() for item in retention_markers
    ):
        raise ValueError(f"context.retention_markers must be a list of non-empty strings: {path}")

    workspace = data.get("workspace", {})
    if not isinstance(workspace, dict):
        workspace = {}
    raw_writable_paths = workspace.get("writable_paths")
    if raw_writable_paths is None:
        writable_paths = None
    elif not isinstance(raw_writable_paths, list):
        raise ValueError(f"workspace.writable_paths must be a list: {path}")
    else:
        writable_paths = tuple(_safe_relative_path(item, path) for item in raw_writable_paths)

    return EvalCase(
        name=str(data.get("name") or case_dir.name),
        prompt=prompt.strip(),
        case_dir=case_dir,
        fixture_dir=fixture_dir,
        sandbox_mode=str(data.get("sandbox_mode") or "locked"),
        capability=str(data.get("capability") or ""),
        proves=str(data.get("proves") or ""),
        budget=budget,
        assertions=[item for item in assertions if isinstance(item, dict)],
        writable_paths=writable_paths,
        context={**context, "retention_markers": list(retention_markers)},
        completion_gate=bool(data.get("completion_gate", False)),
        definition_sha256=hashlib.sha256(source).hexdigest(),
    )


def build_case_authority_profiles(
    cases: Sequence[EvalCase],
    *,
    project_root: Path,
) -> dict[str, dict[str, str]]:
    profiles: dict[str, dict[str, str]] = {}
    for case in cases:
        profiles[case.name] = {
            "source_path": case_source_path(case, project_root=project_root),
            "fixture_source_path": fixture_source_path(
                case,
                project_root=project_root,
            ),
            "case_definition_sha256": case.definition_sha256,
            "fixture_content_sha256": workspace_content_digest(case.fixture_dir),
        }
    return profiles


def case_source_path(case: EvalCase, *, project_root: Path) -> str:
    return _project_relative_path(
        case.case_dir / "case.yaml",
        project_root=project_root,
    )


def fixture_source_path(case: EvalCase, *, project_root: Path) -> str:
    return _project_relative_path(case.fixture_dir, project_root=project_root)


def _project_relative_path(path: Path, *, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return f"external:{resolved.as_posix()}"


def case_authority_bundle_sha256(
    profiles: Mapping[str, Mapping[str, str]],
) -> str:
    payload = {
        "schema_version": 1,
        "cases": {
            str(name): {
                "source_path": str(profile.get("source_path") or ""),
                "fixture_source_path": str(
                    profile.get("fixture_source_path") or ""
                ),
                "case_definition_sha256": str(
                    profile.get("case_definition_sha256") or ""
                ),
                "fixture_content_sha256": str(
                    profile.get("fixture_content_sha256") or ""
                ),
            }
            for name, profile in profiles.items()
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _safe_relative_path(value: Any, case_path: Path) -> str:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    parts = Path(normalized).parts
    if not normalized or Path(normalized).is_absolute() or ".." in parts:
        raise ValueError(f"workspace.writable_paths contains an unsafe path in {case_path}: {value}")
    return normalized
