from minicc.skills.registry import SkillRegistry


def test_skill_registry_reads_frontmatter_catalog(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "python-debugging"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: python-debugging",
                "description: Debug pytest failures.",
                "---",
                "Use pytest output to find the first failure.",
            ]
        ),
        encoding="utf-8",
    )

    registry = SkillRegistry(tmp_path / "skills")
    skills = registry.list_skills()

    assert len(skills) == 1
    assert skills[0].name == "python-debugging"
    assert skills[0].description == "Debug pytest failures."
    assert skills[0].instructions == "Use pytest output to find the first failure."
    assert len(skills[0].sha256) == 64
    assert "python-debugging: Debug pytest failures." in registry.catalog_text()
    assert f"Catalog SHA-256: {registry.catalog_digest}" in registry.catalog_text()


def test_skill_registry_selects_only_goal_relevant_skills(tmp_path) -> None:
    skills_root = tmp_path / "skills"
    for name, description in (
        ("python-debugging", "Debug python pytest failures."),
        ("release-notes", "Write changelog release notes."),
    ):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nFollow the scoped guidance.\n",
            encoding="utf-8",
        )

    registry = SkillRegistry(skills_root)

    assert [skill.name for skill in registry.relevant_skills("Fix a python pytest failure")] == [
        "python-debugging"
    ]
    catalog = registry.catalog_text("Fix a python pytest failure")
    assert "python-debugging" in catalog
    assert "release-notes" not in catalog
    assert registry.catalog_text("Unrelated database migration") == ""
    guidance = registry.guidance_text("Fix a python pytest failure")
    assert "Selected skill instructions:" in guidance
    assert "python-debugging" in guidance


def test_skill_registry_uses_yaml_keywords_for_chinese_goals(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "cache-debugging"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cache-debugging\ndescription: Inspect prompt cache metrics.\n"
        "keywords:\n  - 缓存命中率\n  - prompt cache\n---\nCheck per-request usage.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(tmp_path / "skills")

    assert [skill.name for skill in registry.relevant_skills("分析缓存命中率偏低")] == [
        "cache-debugging"
    ]


def test_skill_registry_source_priority_and_catalog_digest_are_frozen(tmp_path) -> None:
    project_root = tmp_path / "project"
    user_root = tmp_path / "user"
    for root, body in ((project_root, "Project instructions."), (user_root, "User instructions.")):
        skill_dir = root / "shared"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: shared\ndescription: Shared workflow.\n---\n" + body,
            encoding="utf-8",
        )
    registry = SkillRegistry(roots=(("project", project_root), ("user", user_root)))

    first = registry.list_skills()
    digest = registry.catalog_digest
    (project_root / "shared" / "SKILL.md").write_text("changed", encoding="utf-8")

    assert first[0].source == "project"
    assert first[0].instructions == "Project instructions."
    assert registry.catalog_digest == digest
    assert registry.list_skills()[0].instructions == "Project instructions."
