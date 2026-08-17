from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

HARD_DENY_NAMES = {
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
    "target",
    ".minicc",
    ".workbuddy",
    "dist",
    "build",
}

GIT_EXCLUDE_PATTERNS = [
    "**/__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".minicc_artifacts/",
]

BASELINE_REF = "refs/minicc/baseline"


@dataclass(frozen=True)
class RunWorkspace:
    run_id: str
    run_dir: Path
    workspace_dir: Path
    artifacts_dir: Path
    manifest_path: Path
    content_digest_sha256: str


@dataclass(frozen=True)
class WorkspacePathPolicy:
    ignored_allowlist: tuple[str, ...] = ()

    def hard_denied(self, relative_path: str | Path) -> bool:
        relative = _relative_posix(relative_path)
        parts = tuple(part.casefold() for part in PurePosixPath(relative).parts)
        if any(part in HARD_DENY_NAMES for part in parts):
            return True
        name = PurePosixPath(relative).name.casefold()
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            return True
        return name.endswith((".pyc", ".pyo"))

    def allowlisted(self, relative_path: str | Path) -> bool:
        relative = _relative_posix(relative_path)
        for pattern in self.ignored_allowlist:
            normalized = pattern.replace("\\", "/").strip("/")
            if not normalized:
                continue
            if relative == normalized or relative.startswith(f"{normalized}/"):
                return True
            if fnmatch.fnmatchcase(relative, normalized) or PurePosixPath(relative).match(
                normalized
            ):
                return True
        return False


@dataclass(frozen=True)
class _GitSourceSnapshot:
    commit: str
    dirty_patch: bytes
    dirty_tracked_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    source_dirty: bool


def workspace_content_digest(source_dir: Path) -> str:
    return content_digest_from_records(workspace_content_records(source_dir))


def workspace_content_records(
    source_dir: Path,
) -> list[tuple[str, str, bytes]]:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Workspace source directory does not exist: {source_dir}")
    policy = WorkspacePathPolicy()
    return _content_records(
        source_dir,
        _auditable_files(source_dir, policy),
    )


def content_digest_from_records(
    records: Iterable[tuple[str, str, bytes]],
) -> str:
    entries = [
        {
            "path": relative,
            "kind": kind,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for relative, kind, content in sorted(records, key=lambda item: item[0])
    ]
    payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prepare_run_workspace(
    source_dir: Path,
    *,
    run_id: str,
    runs_root: Path | None = None,
    ignored_allowlist: Iterable[str] = (),
    allowlist_source: str | None = None,
) -> RunWorkspace:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Workspace source directory does not exist: {source_dir}")
    allowlist = _validate_allowlist(ignored_allowlist)
    policy = WorkspacePathPolicy(allowlist)
    runs_root = (runs_root or source_dir / ".minicc" / "runs").resolve()
    run_dir = runs_root / run_id
    workspace_dir = run_dir / "workspace"
    artifacts_dir = run_dir / "artifacts"
    manifest_path = run_dir / "workspace_manifest.json"

    git_root = _exact_git_root(source_dir)
    git_snapshot = _inspect_git_source(source_dir) if git_root is not None else None

    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)

    if git_snapshot is not None:
        snapshot_details = _prepare_git_snapshot(
            source_dir,
            workspace_dir,
            git_snapshot,
            policy,
        )
        snapshot_mode = "git"
        source_commit: str | None = git_snapshot.commit
        source_dirty = git_snapshot.source_dirty
        dirty_patch_sha256: str | None = (
            hashlib.sha256(git_snapshot.dirty_patch).hexdigest()
            if git_snapshot.source_dirty
            else None
        )
    else:
        snapshot_details = _prepare_copy_snapshot(source_dir, workspace_dir, policy)
        snapshot_mode = "copy"
        source_commit = None
        source_dirty = False
        dirty_patch_sha256 = None

    baseline_commit = _git_text(workspace_dir, "rev-parse", BASELINE_REF)
    included_files = _auditable_files(workspace_dir, policy)
    content_digest_sha256 = _content_digest(workspace_dir, included_files)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source_dir),
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "dirty_patch_sha256": dirty_patch_sha256,
        "snapshot_mode": snapshot_mode,
        "baseline_commit": baseline_commit,
        "included": {
            **snapshot_details["included"],
            "file_count": len(included_files),
            "path_digest_sha256": _path_digest(included_files),
            "content_digest_sha256": content_digest_sha256,
        },
        "excluded": snapshot_details["excluded"],
        "ignore_rules": {
            "hard_deny_names": sorted(HARD_DENY_NAMES),
            "dotenv_policy": ".env and .env.* except .env.example",
            "ignored_allowlist": list(allowlist),
            "allowlist_source": allowlist_source or "prepare_run_workspace argument",
            "git_ignore_source": (
                "source repository exclude-standard" if snapshot_mode == "git" else "copied .gitignore"
            ),
        },
        "evidence": {
            "manifest": "workspace_manifest.json",
            "workspace": "workspace",
            "state": "state.json",
            "trace": "trace.jsonl",
            "metrics": "metrics.json",
            "diff": "artifacts/diff.patch",
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return RunWorkspace(
        run_id=run_id,
        run_dir=run_dir,
        workspace_dir=workspace_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=manifest_path,
        content_digest_sha256=content_digest_sha256,
    )


def write_workspace_diff(workspace_dir: Path, artifacts_dir: Path) -> Path:
    workspace_dir = workspace_dir.resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    diff_path = artifacts_dir / "diff.patch"
    policy = WorkspacePathPolicy()
    baseline_ref = _workspace_baseline(workspace_dir)
    auditable_files = _auditable_files(workspace_dir, policy)
    for batch in _batches(auditable_files, 100):
        _run_git(workspace_dir, "add", "-N", "-f", "--", *batch)
    result = _run_git(
        workspace_dir,
        "diff",
        "--binary",
        "--no-ext-diff",
        baseline_ref,
        "--",
    )
    diff_path.write_bytes(result.stdout or b"")
    return diff_path


def _prepare_git_snapshot(
    source_dir: Path,
    workspace_dir: Path,
    source: _GitSourceSnapshot,
    policy: WorkspacePathPolicy,
) -> dict[str, dict[str, object]]:
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(source_dir),
            str(workspace_dir),
        ],
        capture_output=True,
        timeout=120,
        check=True,
    )
    _run_git(workspace_dir, "checkout", "--quiet", "--detach", "--force", source.commit)
    _remove_origin(workspace_dir)
    if source.dirty_patch:
        _run_git(
            workspace_dir,
            "apply",
            "--binary",
            "--whitespace=nowarn",
            "-",
            input_data=source.dirty_patch,
        )

    copied_untracked = _copy_source_paths(source_dir, workspace_dir, source.untracked_paths, policy)
    copied_allowlisted = _copy_allowlisted_ignored(source_dir, workspace_dir, policy)
    denied_source_paths = [path for path in source.untracked_paths if policy.hard_denied(path)]
    denied_source_paths.extend(_hard_denied_allowlist_matches(source_dir, policy))
    hard_denied = sorted(
        set(denied_source_paths + _remove_hard_denied_paths(workspace_dir, policy))
    )
    baseline = _commit_baseline(workspace_dir, copied_allowlisted)
    tracked_output = _git_text(workspace_dir, "ls-files")
    tracked_count = int(tracked_output.count("\n") + bool(tracked_output))
    return {
        "included": {
            "tracked_count": tracked_count,
            "dirty_tracked_paths": list(source.dirty_tracked_paths),
            "untracked_paths": copied_untracked,
            "ignored_allowlisted_paths": copied_allowlisted,
            "baseline_commit": baseline,
        },
        "excluded": {
            "hard_denied_paths": hard_denied,
            "ignored_default": "excluded by source Git ignore rules unless allowlisted",
        },
    }


def _prepare_copy_snapshot(
    source_dir: Path,
    workspace_dir: Path,
    policy: WorkspacePathPolicy,
) -> dict[str, dict[str, object]]:
    excluded: list[str] = []

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        directory_path = Path(directory)
        for name in names:
            relative = (directory_path / name).relative_to(source_dir).as_posix()
            source_path = directory_path / name
            if policy.hard_denied(relative) or _symlink_escapes(source_path, source_dir):
                ignored.add(name)
                excluded.append(relative)
        return ignored

    shutil.copytree(source_dir, workspace_dir, ignore=ignore, symlinks=False)
    _run_git(workspace_dir, "init", "--quiet")
    ignored_by_local_rules = _git_zlist(
        workspace_dir,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
    )
    removed_ignored: list[str] = []
    for relative in ignored_by_local_rules:
        if policy.allowlisted(relative) and not policy.hard_denied(relative):
            continue
        target = workspace_dir / relative
        if target.exists() or target.is_symlink():
            _remove_path(target)
            removed_ignored.append(relative)
    hard_denied = sorted(set(excluded + _remove_hard_denied_paths(workspace_dir, policy)))
    baseline = _commit_baseline(workspace_dir, _auditable_files(workspace_dir, policy))
    included = _auditable_files(workspace_dir, policy)
    return {
        "included": {
            "tracked_count": len(included),
            "dirty_tracked_paths": [],
            "untracked_paths": [],
            "ignored_allowlisted_paths": [path for path in ignored_by_local_rules if policy.allowlisted(path)],
            "baseline_commit": baseline,
        },
        "excluded": {
            "hard_denied_paths": hard_denied,
            "ignored_paths": sorted(removed_ignored),
            "ignored_default": "excluded by copied .gitignore unless allowlisted",
        },
    }


def _inspect_git_source(source_dir: Path) -> _GitSourceSnapshot:
    commit = _git_text(source_dir, "rev-parse", "--verify", "HEAD^{commit}")
    patch = _run_git(
        source_dir,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "HEAD",
        "--",
    ).stdout or b""
    dirty_tracked = tuple(_git_zlist(source_dir, "diff", "--name-only", "HEAD", "--"))
    untracked = tuple(_git_zlist(source_dir, "ls-files", "--others", "--exclude-standard"))
    status = _run_git(source_dir, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    return _GitSourceSnapshot(
        commit=commit,
        dirty_patch=patch,
        dirty_tracked_paths=dirty_tracked,
        untracked_paths=untracked,
        source_dirty=bool(status),
    )


def _exact_git_root(source_dir: Path) -> Path | None:
    result = _run_git(source_dir, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return None
    raw = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    if not raw:
        return None
    root = Path(raw).resolve()
    return root if os.path.normcase(str(root)) == os.path.normcase(str(source_dir)) else None


def _copy_source_paths(
    source_dir: Path,
    workspace_dir: Path,
    paths: Sequence[str],
    policy: WorkspacePathPolicy,
) -> list[str]:
    copied: list[str] = []
    for relative in paths:
        if policy.hard_denied(relative):
            continue
        source = source_dir / relative
        if not source.is_file() and not source.is_symlink():
            continue
        target = workspace_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_source = source.resolve() if source.is_symlink() else source
        try:
            copy_source.relative_to(source_dir)
        except ValueError:
            continue
        shutil.copy2(copy_source, target)
        copied.append(_relative_posix(relative))
    return sorted(set(copied))


def _copy_allowlisted_ignored(
    source_dir: Path,
    workspace_dir: Path,
    policy: WorkspacePathPolicy,
) -> list[str]:
    if not policy.ignored_allowlist:
        return []
    candidates = _expand_allowlisted_files(source_dir, policy)
    if not candidates:
        return []
    payload = b"\0".join(path.encode("utf-8") for path in candidates) + b"\0"
    result = _run_git(
        source_dir,
        "check-ignore",
        "-z",
        "--stdin",
        input_data=payload,
        check=False,
    )
    ignored_candidates = {
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in (result.stdout or b"").split(b"\0")
        if item
    }
    selected = sorted(path for path in candidates if path in ignored_candidates)
    return _copy_source_paths(source_dir, workspace_dir, selected, policy)


def _hard_denied_allowlist_matches(
    source_dir: Path,
    policy: WorkspacePathPolicy,
) -> list[str]:
    denied: set[str] = set()
    for child in source_dir.iterdir():
        relative = child.relative_to(source_dir).as_posix()
        if policy.hard_denied(relative) and policy.allowlisted(relative):
            denied.add(relative)
    return sorted(denied)


def _expand_allowlisted_files(
    source_dir: Path,
    policy: WorkspacePathPolicy,
) -> list[str]:
    selected: list[str] = []
    for directory, dir_names, file_names in os.walk(source_dir, topdown=True, followlinks=False):
        directory_path = Path(directory)
        kept_dirs: list[str] = []
        for name in dir_names:
            path = directory_path / name
            relative = path.relative_to(source_dir).as_posix()
            if policy.hard_denied(relative) or _symlink_escapes(path, source_dir):
                continue
            kept_dirs.append(name)
        dir_names[:] = kept_dirs
        for name in file_names:
            path = directory_path / name
            relative = path.relative_to(source_dir).as_posix()
            if (
                policy.allowlisted(relative)
                and not policy.hard_denied(relative)
                and not _symlink_escapes(path, source_dir)
            ):
                selected.append(relative)
    return sorted(set(selected))


def _commit_baseline(workspace_dir: Path, force_add_paths: Sequence[str]) -> str:
    _run_git(workspace_dir, "config", "user.email", "minicc@example.local")
    _run_git(workspace_dir, "config", "user.name", "miniCC")
    exclude_path = workspace_dir / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8", errors="replace") if exclude_path.exists() else ""
    exclude_path.write_text(existing + "\n".join(GIT_EXCLUDE_PATTERNS) + "\n", encoding="utf-8")
    _run_git(workspace_dir, "add", "-A")
    for batch in _batches(sorted(set(force_add_paths)), 100):
        _run_git(workspace_dir, "add", "-f", "--", *batch)
    _run_git(workspace_dir, "commit", "--quiet", "--allow-empty", "-m", "miniCC workspace baseline")
    baseline = _git_text(workspace_dir, "rev-parse", "HEAD")
    _run_git(workspace_dir, "update-ref", BASELINE_REF, baseline)
    return baseline


def _remove_origin(workspace_dir: Path) -> None:
    result = _run_git(workspace_dir, "remote", "get-url", "origin", check=False)
    if result.returncode == 0:
        _run_git(workspace_dir, "remote", "remove", "origin")


def _remove_hard_denied_paths(workspace_dir: Path, policy: WorkspacePathPolicy) -> list[str]:
    removed: list[str] = []
    for child in workspace_dir.iterdir():
        if child.name == ".git":
            continue
        relative = child.relative_to(workspace_dir).as_posix()
        if policy.hard_denied(relative):
            _remove_path(child)
            removed.append(relative)
            continue
        if child.is_dir() and not child.is_symlink():
            for path in sorted(child.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                relative = path.relative_to(workspace_dir).as_posix()
                if policy.hard_denied(relative) and (path.exists() or path.is_symlink()):
                    _remove_path(path)
                    removed.append(relative)
    return sorted(set(removed))


def _auditable_files(workspace_dir: Path, policy: WorkspacePathPolicy) -> list[str]:
    files: list[str] = []
    for directory, dir_names, file_names in os.walk(
        workspace_dir,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        kept_dirs: list[str] = []
        for name in dir_names:
            path = directory_path / name
            relative = path.relative_to(workspace_dir).as_posix()
            if relative == ".git" or policy.hard_denied(relative):
                continue
            if path.is_symlink():
                files.append(relative)
                continue
            kept_dirs.append(name)
        dir_names[:] = kept_dirs
        for name in file_names:
            path = directory_path / name
            relative = path.relative_to(workspace_dir).as_posix()
            if not policy.hard_denied(relative):
                files.append(relative)
    return sorted(files)


def _workspace_baseline(workspace_dir: Path) -> str:
    result = _run_git(workspace_dir, "rev-parse", "--verify", BASELINE_REF, check=False)
    if result.returncode == 0:
        return BASELINE_REF
    roots = _git_text(
        workspace_dir,
        "rev-list",
        "--max-parents=0",
        "--reverse",
        "HEAD",
    ).splitlines()
    if not roots:
        raise RuntimeError(f"Workspace has no Git baseline: {workspace_dir}")
    baseline = roots[0]
    _run_git(workspace_dir, "update-ref", BASELINE_REF, baseline)
    return BASELINE_REF


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _symlink_escapes(path: Path, root: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return True
    return False


def _validate_allowlist(paths: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in paths:
        path = str(raw).replace("\\", "/").strip("/")
        pure = PurePosixPath(path)
        unsafe = (
            not path
            or pure.is_absolute()
            or Path(path).is_absolute()
            or ":" in pure.parts[0]
            or ".." in pure.parts
        )
        if unsafe:
            raise ValueError(f"Unsafe ignored workspace allowlist path: {raw}")
        normalized.append(path)
    return tuple(normalized)


def _relative_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")


def _path_digest(paths: Sequence[str]) -> str:
    payload = "\0".join(paths).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _content_digest(root: Path, paths: Sequence[str]) -> str:
    return content_digest_from_records(_content_records(root, paths))


def _content_records(
    root: Path,
    paths: Sequence[str],
) -> list[tuple[str, str, bytes]]:
    records: list[tuple[str, str, bytes]] = []
    for relative in paths:
        path = root / relative
        if path.is_symlink():
            content = os.readlink(path).encode("utf-8", errors="surrogateescape")
            kind = "symlink"
        else:
            content = path.read_bytes()
            kind = "file"
        records.append((relative, kind, content))
    return records


def _git_zlist(directory: Path, *args: str) -> list[str]:
    if not args:
        return []
    command, *command_args = args
    raw = _run_git(directory, command, "-z", *command_args).stdout or b""
    return sorted(
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in raw.split(b"\0")
        if item
    )


def _git_text(directory: Path, *args: str) -> str:
    result = _run_git(directory, *args)
    return (result.stdout or b"").decode("utf-8", errors="replace").strip()


def _run_git(
    directory: Path,
    *args: str,
    input_data: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        input=input_data,
        capture_output=True,
        timeout=120,
        check=check,
    )


def _batches(paths: Sequence[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(paths), size):
        yield list(paths[start : start + size])
