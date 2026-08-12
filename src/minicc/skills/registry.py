from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    instructions: str
    sha256: str


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

    def relevant_skills(self, goal: str, *, limit: int = 5) -> list[Skill]:
        if limit < 1:
            return []
        goal_terms = _terms(goal)
        if not goal_terms:
            return []
        scored: list[tuple[int, str, Skill]] = []
        for skill in self.list_skills():
            skill_terms = _terms(f"{skill.name} {skill.description}")
            score = len(goal_terms & skill_terms)
            if score:
                scored.append((-score, skill.name, skill))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    def catalog_text(self, goal: str | None = None, *, limit: int = 5) -> str:
        skills = self.relevant_skills(goal, limit=limit) if goal is not None else self.list_skills()[:limit]
        if not skills:
            return ""
        lines = ["Available skills:"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")
        lines.append("Load a skill only when useful by reading its SKILL.md with bash.")
        return "\n".join(lines)

    def guidance_text(self, goal: str, *, limit: int = 5) -> str:
        skills = self.relevant_skills(goal, limit=limit)
        if not skills:
            return ""
        sections = ["Selected skill instructions:"]
        for skill in skills:
            sections.extend(
                [
                    f"## {skill.name}",
                    f"Description: {skill.description}",
                    skill.instructions,
                ]
            )
        return "\n".join(sections)


def _parse_skill_file(path: Path) -> Skill | None:
    if path.is_symlink() or not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    metadata = _frontmatter(text)
    name = metadata.get("name") or path.parent.name
    description = metadata.get("description") or _first_non_empty_body_line(text)
    if not name or not description:
        return None
    instructions = _body(text)[:4_000].strip()
    if not instructions:
        return None
    return Skill(
        name=name,
        description=description,
        path=path,
        instructions=instructions,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


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


def _body(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return ""


def _terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w-]+", text.lower(), flags=re.UNICODE)
        if len(token) >= 3
    }
