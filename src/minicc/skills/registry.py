from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MAX_SKILL_INSTRUCTION_CHARS = 32_000
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    instructions: str
    sha256: str
    source: str = "project"
    keywords: tuple[str, ...] = ()


class SkillRegistry:
    """A run-scoped, immutable catalog of skills from ordered sources."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        roots: Iterable[tuple[str, Path]] | None = None,
    ) -> None:
        if roots is not None:
            self.roots = tuple((str(source), Path(path)) for source, path in roots)
        else:
            self.roots = (("project", root or Path.cwd() / "skills"),)
        self.root = self.roots[0][1]
        self._skills: tuple[Skill, ...] | None = None
        self.errors: list[str] = []

    def list_skills(self) -> list[Skill]:
        self._freeze()
        return list(self._skills or ())

    @property
    def catalog_digest(self) -> str:
        skills = self.list_skills()
        payload = [
            {
                "name": skill.name,
                "description": skill.description,
                "sha256": skill.sha256,
                "source": skill.source,
            }
            for skill in skills
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get(self, name: str) -> Skill | None:
        normalized = name.strip().lower()
        return next((skill for skill in self.list_skills() if skill.name == normalized), None)

    def load_text(self, name: str) -> str | None:
        skill = self.get(name)
        if skill is None:
            return None
        return "\n".join(
            [
                f"Loaded skill: {skill.name}",
                f"Description: {skill.description}",
                f"Source: {skill.source}",
                f"SHA-256: {skill.sha256}",
                "Instructions:",
                skill.instructions,
            ]
        )

    def relevant_skills(self, goal: str, *, limit: int = 5) -> list[Skill]:
        if limit < 1:
            return []
        goal_terms = _terms(goal)
        if not goal_terms:
            return []
        normalized_goal = goal.lower()
        scored: list[tuple[int, str, Skill]] = []
        for skill in self.list_skills():
            name_terms = _terms(skill.name)
            description_terms = _terms(skill.description)
            keyword_terms = _terms(" ".join(skill.keywords))
            score = (
                len(goal_terms & name_terms) * 4
                + len(goal_terms & keyword_terms) * 3
                + len(goal_terms & description_terms) * 2
            )
            if skill.name in normalized_goal:
                score += 20
            if score:
                scored.append((-score, skill.name, skill))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    def catalog_text(self, goal: str | None = None, *, limit: int = 20) -> str:
        skills = (
            self.relevant_skills(goal, limit=limit)
            if goal is not None
            else self.list_skills()[:limit]
        )
        if not skills:
            return ""
        lines = [
            "Available skills (metadata only):",
            f"Catalog SHA-256: {self.catalog_digest}",
        ]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description} [source={skill.source}]")
        lines.append('Load instructions only when needed with {"type":"skill","name":"..."}.')
        return "\n".join(lines)

    def guidance_text(self, goal: str, *, limit: int = 5) -> str:
        """Compatibility helper for explicit callers; prompt assembly uses catalog_text."""
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

    def _freeze(self) -> None:
        if self._skills is not None:
            return
        by_name: dict[str, Skill] = {}
        for source, root in self.roots:
            if not root.exists():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                try:
                    skill = _parse_skill_file(skill_file, source=source)
                except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                    self.errors.append(f"{skill_file}: {exc}")
                    continue
                if skill is not None and skill.name not in by_name:
                    by_name[skill.name] = skill
        self._skills = tuple(sorted(by_name.values(), key=lambda skill: skill.name))


def default_skill_roots(workspace: Path) -> tuple[tuple[str, Path], ...]:
    """Return skill sources in override order: project, agent, user, bundled."""
    package_root = Path(__file__).resolve().parent
    return (
        ("project", workspace / "skills"),
        ("agents", workspace / ".agents" / "skills"),
        ("user", Path.home() / ".minicc" / "skills"),
        ("bundled", package_root / "bundled"),
    )


def _parse_skill_file(path: Path, *, source: str = "project") -> Skill | None:
    if path.is_symlink() or not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    metadata, body = _frontmatter_and_body(text)
    raw_name = metadata.get("name") or path.parent.name
    name = str(raw_name).strip().lower()
    if not _SKILL_NAME.fullmatch(name):
        raise ValueError("skill name must be lowercase letters, numbers, dots, dashes, or underscores")
    raw_description = metadata.get("description") or _first_non_empty_line(body)
    description = str(raw_description).strip()
    if not description or len(description) > 500:
        raise ValueError("skill description must contain 1 to 500 characters")
    instructions = body.strip()
    if not instructions:
        raise ValueError("skill instructions are empty")
    if len(instructions) > MAX_SKILL_INSTRUCTION_CHARS:
        raise ValueError(
            f"skill instructions exceed {MAX_SKILL_INSTRUCTION_CHARS} characters"
        )
    keywords = _keywords(metadata.get("keywords"))
    return Skill(
        name=name,
        description=description,
        path=path,
        instructions=instructions,
        sha256=hashlib.sha256(raw).hexdigest(),
        source=source,
        keywords=keywords,
    )


def _frontmatter_and_body(text: str) -> tuple[Mapping[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise ValueError("skill frontmatter is missing its closing delimiter")
    raw_metadata = "\n".join(lines[1:closing])
    metadata = yaml.safe_load(raw_metadata) if raw_metadata.strip() else {}
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise ValueError("skill frontmatter must be a YAML mapping")
    return metadata, "\n".join(lines[closing + 1 :])


def _keywords(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("skill keywords must be a string or list of strings")
    return tuple(str(item).strip().lower() for item in values if str(item).strip())


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


def _terms(text: str) -> set[str]:
    normalized = text.lower()
    terms = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._-]+", normalized)
        if len(token) >= 2
    }
    for chunk in re.findall(r"[\u3400-\u9fff]+", normalized):
        terms.update(chunk[index : index + 2] for index in range(max(len(chunk) - 1, 0)))
        if len(chunk) == 1:
            terms.add(chunk)
    return terms
