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
