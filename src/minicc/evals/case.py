from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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


def discover_cases(path: Path) -> list[EvalCase]:
    if path.is_file():
        return [load_case(path)]

    case_files = sorted(path.rglob("case.yaml"))
    if not case_files:
        return []
    return [load_case(case_file) for case_file in case_files]


def load_case(path: Path) -> EvalCase:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
    )


def _safe_relative_path(value: Any, case_path: Path) -> str:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    parts = Path(normalized).parts
    if not normalized or Path(normalized).is_absolute() or ".." in parts:
        raise ValueError(f"workspace.writable_paths contains an unsafe path in {case_path}: {value}")
    return normalized
