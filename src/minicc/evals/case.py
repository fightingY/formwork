from __future__ import annotations

import re
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
    context: dict[str, Any] = field(default_factory=dict)
    completion_gate: bool = False
    initial_verify: dict[str, Any] | None = None
    cleanup_workspace: bool = False


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

    initial_verify = data.get("initial_verify")
    if initial_verify is not None:
        if not isinstance(initial_verify, dict):
            raise ValueError(f"initial_verify must be a mapping: {path}")
        verifier_type = str(initial_verify.get("type") or "command")
        if verifier_type not in {"command", "python_verifier"}:
            raise ValueError(f"initial_verify has unsupported type: {path}")
        initial_verify = {"type": verifier_type, **initial_verify}
        if verifier_type == "command" and not str(initial_verify.get("command") or "").strip():
            raise ValueError(f"initial_verify requires a command: {path}")
        if verifier_type == "python_verifier":
            verifier_path = initial_verify.get("path")
            if not isinstance(verifier_path, str):
                raise ValueError(f"initial_verify python_verifier requires path: {path}")
            _safe_relative_path(verifier_path, path)
            digest = str(initial_verify.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"initial_verify python_verifier requires a SHA-256 digest: {path}")
            initial_verify["sha256"] = digest
        try:
            expected_initial_exit = int(initial_verify.get("expect_exit_code", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"initial_verify.expect_exit_code must be an integer: {path}") from exc
        if expected_initial_exit == 0:
            raise ValueError(f"initial_verify must expect a failing command: {path}")
        initial_verify["expect_exit_code"] = expected_initial_exit

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
        initial_verify=initial_verify,
        cleanup_workspace=bool(data.get("cleanup_workspace", False)),
    )


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


def _safe_relative_path(value: Any, case_path: Path) -> str:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    parts = Path(normalized).parts
    if not normalized or Path(normalized).is_absolute() or ".." in parts:
        raise ValueError(f"workspace.writable_paths contains an unsafe path in {case_path}: {value}")
    return normalized
