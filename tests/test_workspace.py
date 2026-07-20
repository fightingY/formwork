import json
import subprocess
from pathlib import Path

import pytest

from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _init_git_project(project: Path) -> str:
    _git(project, "init")
    _git(project, "config", "user.email", "tests@example.local")
    _git(project, "config", "user.name", "miniCC tests")
    _git(project, "add", "-A")
    _git(project, "commit", "-m", "initial")
    return _git(project, "rev-parse", "HEAD")


def test_prepare_run_workspace_copies_source_and_ignores_runtime_dirs(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "src").mkdir()
    (source / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (source / ".env").write_text("MINICC_API_KEY=secret\n", encoding="utf-8")
    (source / ".env.example").write_text("MINICC_API_KEY=example\n", encoding="utf-8")
    (source / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (source / "developer.local").write_text("machine-specific\n", encoding="utf-8")
    (source / ".minicc").mkdir()
    (source / ".minicc" / "old.txt").write_text("old", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "pkg.txt").write_text("pkg", encoding="utf-8")

    workspace = prepare_run_workspace(source, run_id="run123")

    assert (workspace.workspace_dir / "src" / "app.py").exists()
    assert not (workspace.workspace_dir / ".env").exists()
    assert (workspace.workspace_dir / ".env.example").exists()
    assert not (workspace.workspace_dir / "developer.local").exists()
    assert not (workspace.workspace_dir / ".minicc").exists()
    assert not (workspace.workspace_dir / "node_modules").exists()
    assert (workspace.workspace_dir / ".git").exists()


def test_write_workspace_diff_writes_patch(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text("before\n", encoding="utf-8")
    workspace = prepare_run_workspace(source, run_id="run456")
    (workspace.workspace_dir / "app.py").write_text("after\n", encoding="utf-8")

    diff_path = write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir)

    diff = Path(diff_path).read_text(encoding="utf-8")
    assert "-before" in diff
    assert "+after" in diff


def test_write_workspace_diff_includes_untracked_files(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    workspace = prepare_run_workspace(source, run_id="run-new-file")
    (workspace.workspace_dir / "ONBOARDING.md").write_text("入口\n", encoding="utf-8")
    cache_dir = workspace.workspace_dir / "src" / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "app.cpython-311.pyc").write_bytes(b"generated")

    diff_path = write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir)
    diff = Path(diff_path).read_text(encoding="utf-8")

    assert "diff --git a/ONBOARDING.md b/ONBOARDING.md" in diff
    assert "+入口" in diff
    assert "__pycache__" not in diff
    assert ".pyc" not in diff


def test_git_snapshot_preserves_tracked_file_that_is_now_ignored(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "generated.txt").write_text("before\n", encoding="utf-8")
    _init_git_project(source)
    (source / ".gitignore").write_text("generated.txt\n", encoding="utf-8")
    _git(source, "add", ".gitignore")
    _git(source, "commit", "-m", "ignore generated output")

    workspace = prepare_run_workspace(source, run_id="tracked-ignored")

    assert _git(workspace.workspace_dir, "ls-files", "--", "generated.txt") == "generated.txt"
    (workspace.workspace_dir / "generated.txt").write_text("after\n", encoding="utf-8")
    diff = write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir).read_text(
        encoding="utf-8"
    )
    assert "diff --git a/generated.txt b/generated.txt" in diff
    assert "-before" in diff
    assert "+after" in diff


def test_git_snapshot_applies_dirty_state_and_records_manifest(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text("clean\n", encoding="utf-8")
    source_commit = _init_git_project(source)
    (source / "app.py").write_text("dirty\n", encoding="utf-8")
    (source / "notes.txt").write_text("untracked\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (source / ".workbuddy").mkdir()
    (source / ".workbuddy" / "notes.md").write_text("noise\n", encoding="utf-8")

    workspace = prepare_run_workspace(source, run_id="dirty-source")

    assert (workspace.workspace_dir / "app.py").read_text(encoding="utf-8") == "dirty\n"
    assert (workspace.workspace_dir / "notes.txt").read_text(encoding="utf-8") == "untracked\n"
    assert not (workspace.workspace_dir / ".env").exists()
    assert not (workspace.workspace_dir / ".workbuddy").exists()
    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["snapshot_mode"] == "git"
    assert manifest["source_commit"] == source_commit
    assert manifest["source_dirty"] is True
    assert len(manifest["dirty_patch_sha256"]) == 64
    assert "app.py" in manifest["included"]["dirty_tracked_paths"]
    assert "notes.txt" in manifest["included"]["untracked_paths"]
    assert manifest["baseline_commit"] == _git(
        workspace.workspace_dir, "rev-parse", "refs/minicc/baseline"
    )
    assert write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir).read_text(
        encoding="utf-8"
    ) == ""


def test_ignored_allowlist_is_explicit_and_sensitive_deny_wins(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / ".gitignore").write_text("generated/\n.env\n", encoding="utf-8")
    (source / "app.py").write_text("clean\n", encoding="utf-8")
    _init_git_project(source)
    (source / "generated").mkdir()
    (source / "generated" / "runtime.json").write_text("{}\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    workspace = prepare_run_workspace(
        source,
        run_id="allow-ignored",
        ignored_allowlist=("generated/runtime.json", ".env"),
        allowlist_source="test configuration",
    )

    assert (workspace.workspace_dir / "generated" / "runtime.json").exists()
    assert not (workspace.workspace_dir / ".env").exists()
    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert manifest["ignore_rules"]["ignored_allowlist"] == [
        "generated/runtime.json",
        ".env",
    ]
    assert manifest["ignore_rules"]["allowlist_source"] == "test configuration"
    assert ".env" in manifest["excluded"]["hard_denied_paths"]


def test_nested_eval_fixture_does_not_snapshot_parent_git_repository(tmp_path) -> None:
    project = tmp_path / "project"
    fixture = project / "eval_cases" / "case" / "fixture"
    fixture.mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "docs" / "history.md").write_text("not fixture\n", encoding="utf-8")
    (fixture / "app.py").write_text("fixture\n", encoding="utf-8")
    _init_git_project(project)

    workspace = prepare_run_workspace(
        fixture,
        run_id="fixture-only",
        runs_root=tmp_path / "runs",
    )

    assert (workspace.workspace_dir / "app.py").exists()
    assert not (workspace.workspace_dir / "docs").exists()
    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert manifest["snapshot_mode"] == "copy"
    assert manifest["source_root"] == str(fixture.resolve())


def test_diff_is_anchored_when_agent_commits_changes(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text("before\n", encoding="utf-8")
    _init_git_project(source)
    workspace = prepare_run_workspace(source, run_id="agent-commit")
    (workspace.workspace_dir / "app.py").write_text("after\n", encoding="utf-8")
    _git(workspace.workspace_dir, "add", "app.py")
    _git(workspace.workspace_dir, "commit", "-m", "agent committed change")

    diff = write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir).read_text(
        encoding="utf-8"
    )

    assert "diff --git a/app.py b/app.py" in diff
    assert "-before" in diff
    assert "+after" in diff


def test_diff_audits_new_gitignored_files_but_skips_generated_cache(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (source / "app.py").write_text("before\n", encoding="utf-8")
    _init_git_project(source)
    workspace = prepare_run_workspace(source, run_id="ignored-output")
    (workspace.workspace_dir / "audit.log").write_text("agent output\n", encoding="utf-8")
    cache = workspace.workspace_dir / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "app.pyc").write_bytes(b"cache")

    diff = write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir).read_text(
        encoding="utf-8"
    )

    assert "diff --git a/audit.log b/audit.log" in diff
    assert "+agent output" in diff
    assert "__pycache__" not in diff
    assert "app.pyc" not in diff


def test_workspace_allowlist_rejects_path_escape(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()

    with pytest.raises(ValueError, match="Unsafe ignored workspace allowlist"):
        prepare_run_workspace(source, run_id="unsafe", ignored_allowlist=("../secret",))
