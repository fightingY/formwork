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
    )
