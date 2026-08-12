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
    assert "python-debugging: Debug pytest failures." in registry.catalog_text()


def test_skill_registry_selects_only_goal_relevant_skills(tmp_path) -> None:
    skills_root = tmp_path / "skills"
    for name, description in (
        ("python-debugging", "Debug python pytest failures."),
        ("release-notes", "Write changelog release notes."),
    ):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n",
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
