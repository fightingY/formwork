from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_IGNORE_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".minicc",
    ".env",
    "dist",
    "build",
}


@dataclass(frozen=True)
class RunWorkspace:
    run_id: str
    run_dir: Path
    workspace_dir: Path
    artifacts_dir: Path


def prepare_run_workspace(
    source_dir: Path,
    *,
    run_id: str,
    runs_root: Path | None = None,
) -> RunWorkspace:
    source_dir = source_dir.resolve()
    runs_root = (runs_root or source_dir / ".minicc" / "runs").resolve()
    run_dir = runs_root / run_id
    workspace_dir = run_dir / "workspace"
    artifacts_dir = run_dir / "artifacts"

    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)

    shutil.copytree(
        source_dir,
        workspace_dir,
        ignore=_ignore_names,
        dirs_exist_ok=False,
    )
    _git_init_snapshot(workspace_dir)

    return RunWorkspace(
        run_id=run_id,
        run_dir=run_dir,
        workspace_dir=workspace_dir,
        artifacts_dir=artifacts_dir,
    )


def write_workspace_diff(workspace_dir: Path, artifacts_dir: Path) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    diff_path = artifacts_dir / "diff.patch"
    subprocess.run(
        ["git", "-C", str(workspace_dir), "add", "-N", "--", "."],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    result = subprocess.run(
        ["git", "-C", str(workspace_dir), "diff", "--no-ext-diff"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    diff_path.write_text(result.stdout or "", encoding="utf-8")
    return diff_path


def _ignore_names(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in DEFAULT_IGNORE_NAMES:
            ignored.add(name)
        elif name.startswith(".env.") and name != ".env.example":
            ignored.add(name)
        elif name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


def _git_init_snapshot(workspace_dir: Path) -> None:
    commands = [
        ["git", "-C", str(workspace_dir), "init"],
        ["git", "-C", str(workspace_dir), "config", "user.email", "minicc@example.local"],
        ["git", "-C", str(workspace_dir), "config", "user.name", "miniCC"],
        ["git", "-C", str(workspace_dir), "add", "-A"],
        ["git", "-C", str(workspace_dir), "commit", "--allow-empty", "-m", "Initial workspace snapshot"],
    ]
    for command in commands:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
