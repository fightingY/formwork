from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

FeedbackType = Literal["never", "prefer", "caution"]


@dataclass(frozen=True)
class FeedbackRule:
    id: str
    type: FeedbackType
    rule: str
    scope: str = "project"


class FeedbackMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.cwd() / ".minicc" / "memory" / "feedback_rules.jsonl"

    def load_rules(self) -> list[FeedbackRule]:
        if not self.path.exists():
            return []

        rules: list[FeedbackRule] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            rule = _rule_from_payload(payload)
            if rule is not None:
                rules.append(rule)
        return rules

    def relevant_rules(self, goal: str, *, limit: int = 10) -> list[FeedbackRule]:
        rules = self.load_rules()
        if not rules:
            return []

        goal_terms = _terms(goal)
        scored: list[tuple[int, int, FeedbackRule]] = []
        for index, rule in enumerate(rules):
            rule_terms = _terms(rule.rule)
            score = len(goal_terms & rule_terms)
            if score > 0 or not goal_terms:
                scored.append((score, -index, rule))

        if not scored:
            return []

        scored.sort(reverse=True)
        return [item[2] for item in scored[:limit]]

    def context_text(self, goal: str, *, limit: int = 10) -> str:
        rules = self.relevant_rules(goal, limit=limit)
        if not rules:
            return ""
        lines = ["Feedback memory rules:"]
        for rule in rules:
            lines.append(f"- {rule.type}: {rule.rule}")
        return "\n".join(lines)

    def selected_rule_ids(self, goal: str, *, limit: int = 10) -> list[str]:
        return [rule.id for rule in self.relevant_rules(goal, limit=limit)]


def _rule_from_payload(payload: Any) -> FeedbackRule | None:
    if not isinstance(payload, dict):
        return None
    rule_type = payload.get("type")
    if rule_type not in {"never", "prefer", "caution"}:
        return None
    rule = payload.get("rule")
    if not isinstance(rule, str) or not rule.strip():
        return None
    rule_id = payload.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        rule_id = f"mem_{abs(hash(rule))}"
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        scope = "project"
    return FeedbackRule(id=rule_id, type=rule_type, rule=rule.strip(), scope=scope.strip())


def _terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w-]+", text.lower(), flags=re.UNICODE)
        if len(token) >= 3
    }
