from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


class SkillRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / "skills"

    def list_skills(self) -> list[Skill]:
        if not self.root.exists():
            return []

        skills: list[Skill] = []
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            skill = _parse_skill_file(skill_file)
            if skill is not None:
                skills.append(skill)
        return skills

    def catalog_text(self, *, limit: int = 20) -> str:
        skills = self.list_skills()[:limit]
        if not skills:
            return ""
        lines = ["Available skills:"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")
        lines.append("Load a skill only when useful by reading its SKILL.md with bash.")
        return "\n".join(lines)


def _parse_skill_file(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    metadata = _frontmatter(text)
    name = metadata.get("name") or path.parent.name
    description = metadata.get("description") or _first_non_empty_body_line(text)
    if not name or not description:
        return None
    return Skill(name=name, description=description, path=path)


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def _first_non_empty_body_line(text: str) -> str:
    in_frontmatter = text.startswith("---")
    for line in text.splitlines()[1 if in_frontmatter else 0 :]:
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
            continue
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""
