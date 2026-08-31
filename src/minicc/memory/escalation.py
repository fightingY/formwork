"""L2/L3 escalation: threshold-triggered synthesis into scenario and persona.

V5.1 memory redesign (``docs/V5_1_MEMORY_REDESIGN_PLAN.md``) P1 + P2.  Above the
per-turn L1 distillation sit two *escalation* passes, both threshold-triggered
over ``scope:project`` L1 memories (plan §3.1):

- **L3 persona** — synthesized once ``preference``/``constraint`` signals are
  confirmed enough times (default ≥3) or the user emphasizes them explicitly
  (``以后都``/``记得``/``规则是``).
- **L2 scenario** — synthesized once a single topic (clustered by ``source.file``)
  accumulates enough L1 memories (default ≥5), producing ``{scenario, summary, recipe}``.

Both are best effort: a synthesis failure records a ``*_synthesis_failed`` metric
and leaves the threshold intact; they never raise.  The two also share one
composite ``EscalationHook`` so the turn-end seam runs them together.
"""

from __future__ import annotations

import json
from typing import Any

from minicc.core.provider import CompletionOptions, ModelProvider, ProviderError
from minicc.memory.l1 import L1Memory, MemoryStore, PersonaEntry, ScenarioEntry

# Explicit emphasis markers that can trigger persona synthesis even before the
# confirmation-count threshold is reached (plan §3.1).
EMPHASIS_MARKERS: tuple[str, ...] = ("以后都", "记得", "规则是", "总是", "从来不", "记住")

# Only these L1 types feed persona synthesis (plan §3.1: "只用 preference/constraint
# 两类 L1, 输入面窄").
PERSONA_SIGNAL_TYPES: frozenset[str] = frozenset({"preference", "constraint"})

_PERSONA_SYSTEM = (
    "You summarize what a user cares about long-term into a compact persona. "
    "Return ONLY a JSON object, no prose."
)

_SCENARIO_SYSTEM = (
    "You distill a set of related coding memories into one reusable project scenario. "
    "Return ONLY a JSON object, no prose."
)


def has_emphasis(text: str) -> bool:
    return any(marker in text for marker in EMPHASIS_MARKERS)


def render_persona(entries: list[PersonaEntry]) -> str:
    """Render the merged persona view (manual first, then auto).

    Manual entries precede auto entries so a human-written rule is read before —
    and therefore wins over — an auto-distilled one (plan §3.1: 手写优先级更高).
    """
    if not entries:
        return ""
    lines = ["Persona (long-term, project-scoped):"]
    for entry in entries:
        origin = "manual" if entry.origin == "manual" else "auto"
        if entry.profile.strip():
            lines.append(f"- profile[{origin}]: {entry.profile.strip()}")
        if entry.style.strip():
            lines.append(f"- style[{origin}]: {entry.style.strip()}")
        if entry.hard_rule.strip():
            lines.append(f"- hard_rule[{origin}]: {entry.hard_rule.strip()}")
    return "\n".join(lines)


class PersonaSynthesizer:
    """One ``json_mode`` LLM call → a ``PersonaEntry`` (never raises).

    ``synthesize`` returns ``None`` on provider or JSON failure so the escalation
    hook records ``persona_synthesis_failed`` and skips (plan §4.5).
    """

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def synthesize(self, signals: list[L1Memory]) -> PersonaEntry | None:
        if not signals:
            return None
        messages = [
            {"role": "system", "content": _PERSONA_SYSTEM},
            {"role": "user", "content": _persona_prompt(signals)},
        ]
        try:
            response = self.provider.complete(
                messages,
                options=CompletionOptions(json_mode=True, max_tokens=None),
            )
        except (ProviderError, RuntimeError):
            return None
        return self._parse(response.text, signals=signals)

    def _parse(self, text: str, *, signals: list[L1Memory]) -> PersonaEntry | None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        profile = _clean(data.get("profile"))
        style = _clean(data.get("style"))
        hard_rule = _clean(data.get("hard_rule"))
        if not any((profile, style, hard_rule)):
            return None
        return PersonaEntry(
            profile=profile,
            style=style,
            hard_rule=hard_rule,
            source_record_ids=[m.record_id for m in signals if m.record_id is not None],
            origin="auto",
            confidence=min(1.0, len(signals) / 5.0),
        )


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


class PersonaEscalator:
    """The turn-end escalation seam, wired into ``MemoryTurnHook.escalator``.

    Runs the threshold check over the persisted project-signal memories (which
    already include this turn's freshly stored memories), and — only when the
    threshold or an emphasis marker fires — synthesizes and upserts one L3 entry.
    A miss costs a single table read and no LLM call (plan §4.1: 没命中就零成本跳过).
    """

    def __init__(
        self,
        store: MemoryStore,
        synthesizer: PersonaSynthesizer,
        *,
        persona_threshold: int = 3,
    ) -> None:
        self.store = store
        self.synthesizer = synthesizer
        self.persona_threshold = max(1, persona_threshold)

    def __call__(self, session_id: str, state: Any, new_memories: list[L1Memory]) -> None:
        del session_id
        self._maybe_synthesize(state, new_memories)

    def _maybe_synthesize(self, state: Any, new_memories: list[L1Memory]) -> None:
        metrics = getattr(state, "metrics", None) or {}
        goal = str(getattr(state, "goal", "") or "")
        signals = self._project_signals()
        if not signals:
            return
        new_signal_content = {
            memory.content
            for memory in new_memories
            if memory.type in PERSONA_SIGNAL_TYPES
        }
        if new_memories and not new_signal_content and not has_emphasis(goal):
            return
        emphasized = has_emphasis(goal)
        if len(signals) < self.persona_threshold and not emphasized:
            return
        entry = self.synthesizer.synthesize(signals)
        if entry is None:
            metrics["persona_synthesis_failed"] = int(
                metrics.get("persona_synthesis_failed", 0)
            ) + 1
            return
        try:
            self.store.upsert_persona(entry)
            _record_memory_event(
                state,
                "memory/l3_upserted",
                {"source_record_ids": entry.source_record_ids, "confidence": entry.confidence},
            )
        except Exception:  # noqa: BLE001 — degrade, never block the turn
            metrics["persona_synthesis_failed"] = int(
                metrics.get("persona_synthesis_failed", 0)
            ) + 1
            return
        metrics["persona_synthesized"] = int(metrics.get("persona_synthesized", 0)) + 1
        metrics["persona_signal_count"] = len(signals)

    def _project_signals(self) -> list[L1Memory]:
        try:
            memories = self.store.list_memories(scope="project")
        except Exception:  # noqa: BLE001 — degrade, no synthesis
            return []
        return [m for m in memories if m.type in PERSONA_SIGNAL_TYPES]


def _persona_prompt(signals: list[L1Memory]) -> str:
    lines = [
        "Distill the following confirmed preference/constraint memories into a concise persona.",
        'Return JSON: {"profile": "<who the user is>", "style": "<how they like to work>",',
        ' "hard_rule": "<non-negotiable constraints>"}',
        "Use short phrases; each field may be empty if nothing applies.",
        "Memories:",
    ]
    for memory in signals:
        lines.append(f"- ({memory.type}, p{memory.priority}) {memory.content}")
    return "\n".join(lines)


# --- L2 scenario ------------------------------------------------------------


def topic_key(memory: L1Memory) -> str:
    """Return a stable L2 topic bucket from provenance metadata.

    File/module/symbol metadata is preferred because it is deterministic and
    explainable.  A semantic ``topic`` supplied by the distiller is accepted as
    a fallback; memories with no project anchor are intentionally not clustered.
    """
    source = memory.source if isinstance(memory.source, dict) else {}
    for key in ("topic", "module", "file", "symbol", "test_name", "git_commit"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\\", "/").lower()
    metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
    value = metadata.get("topic")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return ""


def render_scenarios(entries: list[ScenarioEntry]) -> str:
    """Render L2 scenarios for the system cache track."""
    if not entries:
        return ""
    lines = ["Scenarios (long-term project knowledge):"]
    for entry in entries:
        lines.append(f"- scenario: {entry.scenario.strip()}")
        if entry.summary.strip():
            lines.append(f"  summary: {entry.summary.strip()}")
        if entry.recipe.strip():
            lines.append(f"  recipe: {entry.recipe.strip()}")
    return "\n".join(lines)


class ScenarioSynthesizer:
    """One ``json_mode`` LLM call → a ``ScenarioEntry`` (never raises)."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def synthesize(self, topic: str, memories: list[L1Memory]) -> ScenarioEntry | None:
        if not memories:
            return None
        messages = [
            {"role": "system", "content": _SCENARIO_SYSTEM},
            {"role": "user", "content": _scenario_prompt(topic, memories)},
        ]
        try:
            response = self.provider.complete(
                messages,
                options=CompletionOptions(json_mode=True, max_tokens=None),
            )
        except (ProviderError, RuntimeError):
            return None
        return self._parse(response.text, topic=topic, memories=memories)

    def _parse(
        self,
        text: str,
        *,
        topic: str,
        memories: list[L1Memory],
    ) -> ScenarioEntry | None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        scenario = _clean(data.get("scenario")) or topic
        summary = _clean(data.get("summary"))
        recipe = _clean(data.get("recipe"))
        if not summary and not recipe:
            return None
        return ScenarioEntry(
            scenario=scenario,
            summary=summary,
            recipe=recipe,
            source_record_ids=[m.record_id for m in memories if m.record_id is not None],
        )


def _scenario_prompt(topic: str, memories: list[L1Memory]) -> str:
    lines = [
        f"Distill these memories (all about topic '{topic}') into one reusable scenario.",
        'Return JSON: {"scenario": "<short topic name>", "summary": "<what we know>",',
        ' "recipe": "<how to fix / what to run next time>"}',
        "Use short phrases; a field may be empty if nothing applies.",
        "Memories:",
    ]
    for memory in memories:
        lines.append(f"- ({memory.type}) {memory.content}")
    return "\n".join(lines)


class ScenarioEscalator:
    """Turn-end L2 escalation, keyed by ``source.file`` topic clustering."""

    def __init__(
        self,
        store: MemoryStore,
        synthesizer: ScenarioSynthesizer,
        *,
        scenario_threshold: int = 5,
    ) -> None:
        self.store = store
        self.synthesizer = synthesizer
        self.scenario_threshold = max(1, scenario_threshold)

    def __call__(self, session_id: str, state: Any, new_memories: list[L1Memory]) -> None:
        del session_id
        self._maybe_synthesize(state, new_memories)

    def _maybe_synthesize(self, state: Any, new_memories: list[L1Memory]) -> None:
        metrics = getattr(state, "metrics", None) or {}
        new_ids = {memory.content for memory in new_memories}
        for topic, memories in self._clusters().items():
            cluster_ids = {memory.content for memory in memories}
            # Avoid paying for a repeated synthesis when this turn added no
            # memory to the topic. A new fact causes an in-place L2 refresh.
            if new_ids and not (new_ids & cluster_ids):
                continue
            if len(memories) < self.scenario_threshold:
                continue
            entry = self.synthesizer.synthesize(topic, memories)
            if entry is None:
                metrics["scenario_synthesis_failed"] = int(
                    metrics.get("scenario_synthesis_failed", 0)
                ) + 1
                continue
            try:
                self.store.upsert_scenario(entry)
                _record_memory_event(
                    state,
                    "memory/l2_upserted",
                    {
                        "scenario": entry.scenario,
                        "source_record_ids": entry.source_record_ids,
                    },
                )
            except Exception:  # noqa: BLE001 — degrade, never block
                metrics["scenario_synthesis_failed"] = int(
                    metrics.get("scenario_synthesis_failed", 0)
                ) + 1
                continue
            metrics["scenario_synthesized"] = int(
                metrics.get("scenario_synthesized", 0)
            ) + 1
        try:
            metrics["scenario_count"] = len(self.store.list_scenarios())
        except Exception:  # noqa: BLE001
            metrics["scenario_count"] = 0

    def _clusters(self) -> dict[str, list[L1Memory]]:
        try:
            memories = self.store.list_memories(scope="project")
        except Exception:  # noqa: BLE001 — degrade, no synthesis
            return {}
        clusters: dict[str, list[L1Memory]] = {}
        for memory in memories:
            key = topic_key(memory)
            if not key:
                continue
            clusters.setdefault(key, []).append(memory)
        return clusters


class EscalationHook:
    """Composite turn-end escalation: runs L3 persona then L2 scenario.

    This is the object actually wired into ``MemoryTurnHook.escalator``; each
    sub-escalator is independently best-effort so one failing never blocks the
    other or the turn.
    """

    def __init__(
        self,
        *,
        persona: Any = None,
        scenario: Any = None,
    ) -> None:
        self.persona = persona
        self.scenario = scenario

    def __call__(self, session_id: str, state: Any, new_memories: list[L1Memory]) -> None:
        if self.persona is not None:
            self.persona(session_id, state, new_memories)
        if self.scenario is not None:
            self.scenario(session_id, state, new_memories)


def _record_memory_event(state: Any, event_type: str, data: dict[str, Any]) -> None:
    event_log = getattr(state, "_event_log", None)
    if event_log is None:
        return
    try:
        event_log.append(event_type, {"run_id": getattr(state, "run_id", ""), **data})
    except Exception:
        pass
