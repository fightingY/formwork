from pathlib import Path

from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff


def test_prepare_run_workspace_copies_source_and_ignores_runtime_dirs(tmp_path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "src").mkdir()
    (source / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (source / ".env").write_text("MINICC_API_KEY=secret\n", encoding="utf-8")
    (source / ".env.example").write_text("MINICC_API_KEY=example\n", encoding="utf-8")
    (source / ".minicc").mkdir()
    (source / ".minicc" / "old.txt").write_text("old", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "pkg.txt").write_text("pkg", encoding="utf-8")

    workspace = prepare_run_workspace(source, run_id="run123")

    assert (workspace.workspace_dir / "src" / "app.py").exists()
    assert not (workspace.workspace_dir / ".env").exists()
    assert (workspace.workspace_dir / ".env.example").exists()
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
