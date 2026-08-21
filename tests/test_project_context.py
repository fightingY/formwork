from __future__ import annotations

import json
import os

from minicc.core.project_context import (
    PROJECT_GUIDE_MAX_CHARS,
    inspect_repository,
    load_project_guide,
    write_repository_profile,
)


def test_inspector_builds_stable_bounded_maven_profile(tmp_path) -> None:
    (tmp_path / "pom.xml").write_text("<project />\n", encoding="utf-8")
    (tmp_path / "src" / "main" / "java").mkdir(parents=True)
    (tmp_path / "src" / "test" / "java").mkdir(parents=True)
    (tmp_path / "src" / "test" / "java" / "UserTest.java").write_text("class UserTest {}\n", encoding="utf-8")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    guide_text = "Run the Maven test command.\n"
    (tmp_path / "MINICC.md").write_text(guide_text, encoding="utf-8")

    profile = inspect_repository(tmp_path)

    assert profile.workspace_kind == "maven"
    assert profile.build_files == ("pom.xml",)
    assert profile.candidate_test_commands == ("mvn test",)
    assert profile.source_directories == ("src",)
    assert profile.test_files == ("src/test/java/UserTest.java",)
    assert "target/ignored.txt" not in profile.test_files
    assert profile.guide_status == "loaded"
    assert profile.guide is not None
    assert profile.guide.text == guide_text

    first = tmp_path / "profile-1.json"
    second = tmp_path / "profile-2.json"
    assert write_repository_profile(profile, first) == write_repository_profile(profile, second)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8"))["schema_version"] == 1


def test_project_guide_is_bounded_and_records_truncation(tmp_path) -> None:
    (tmp_path / "MINICC.md").write_text("x" * (PROJECT_GUIDE_MAX_CHARS + 10), encoding="utf-8")

    guide, status = load_project_guide(tmp_path)

    assert guide is not None
    assert status == "loaded_truncated"
    assert guide.truncated is True
    assert len(guide.text) == PROJECT_GUIDE_MAX_CHARS


def test_project_guide_symlink_is_rejected_when_supported(tmp_path) -> None:
    target = tmp_path / "real-guide.md"
    target.write_text("do not inject links\n", encoding="utf-8")
    link = tmp_path / "MINICC.md"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        # Windows CI/dev shells may not grant symlink privilege. Keep the test
        # deterministic by verifying the ordinary file path remains loadable.
        link.write_text("ordinary guide\n", encoding="utf-8")
        guide, status = load_project_guide(tmp_path)
        assert guide is not None
        assert status == "loaded"
        return

    guide, status = load_project_guide(tmp_path)

    assert guide is None
    assert status == "symlink_rejected"
