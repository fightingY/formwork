"""L2/L3 escalation: threshold-triggered synthesis into scenario and persona.

V5.1 memory redesign (``docs/V5_1_MEMORY_REDESIGN_PLAN.md``) P1 + P2.  Above the
per-turn L1 distillation sit two *escalation* passes, both consuming L1
directly — never each other (plan §3.1):

- **L3 rules** — consume only ``preference``/``constraint`` L1.  A synthesis
  produces *candidate* rule rows keyed by ``rule_key``; a rule is confirmed by
  recurrence (seen again on a later pass) or an explicit user confirmation
  (``确认记住``).  A candidate is never injected into prompts, and a hard rule
  additionally requires the explicit confirmation — candidates are advice for
  the prompt track, never an enforcement path (PolicyChain still wins).
- **L2 scenarios** — consume only technical L1 (``fact``/``decision``/
  ``constraint``/``todo``), clustered by the *persisted* ``topic_key`` column.
  A topic escalates once it has enough memories (default ≥5) drawn from at
  least two distinct runs, producing ``{scenario, summary, recipe}`` with an
  explicit ``scenario_members`` relation back to the L1 rows.

Both are best effort: a synthesis failure records a ``*_synthesis_failed``
metric plus a ``memory/l{2,3}_failed`` event and leaves the threshold intact;
they never raise.  The two also share one composite ``EscalationHook`` so the
turn-end seam runs them together.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from minicc.core.provider import CompletionOptions, ModelProvider, ProviderError
from minicc.memory.l1 import (
    L1Memory,
    MemoryStore,
    PersonaEntry,
    ScenarioEntry,
    derive_topic_key,
    recipe_steps,
)

# Explicit emphasis markers that can trigger persona synthesis even before the
# confirmation-count threshold is reached (plan §3.1).
EMPHASIS_MARKERS: tuple[str, ...] = (
    "以后都",
    "记得",
    "规则是",
    "总是",
    "从来不",
    "记住",
    "不要再",
)

# Only an explicit confirmation phrase confirms a rule on the spot; plain
# emphasis only triggers candidate generation (plan §5: 生成 candidate !=
# 立即成为 confirmed rule).
CONFIRM_MARKERS: tuple[str, ...] = ("确认记住",)

# A non-hard rule is confirmed once it has been synthesized on this many
# separate passes (i.e. the same constraint re-appeared later).
L3_CONFIRM_THRESHOLD = 2

# Only these L1 types feed persona synthesis (plan §3.1: "只用 preference/constraint
# 两类 L1, 输入面窄").
PERSONA_SIGNAL_TYPES: frozenset[str] = frozenset({"preference", "constraint"})

# Only technical L1 types feed scenario synthesis — a *preference* is a rule
# signal (L3), never part of a scenario recipe (plan §5: L2 只消费技术事实).
SCENARIO_TYPES: frozenset[str] = frozenset({"fact", "decision", "constraint", "todo"})

# A scenario must draw on facts from at least this many distinct runs, so one
# turn's repeated output cannot conjure one.
MIN_SCENARIO_RUNS = 2

MAX_SCENARIO_INPUTS = 20

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


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _project_id(state: Any) -> str:
    return str(
        getattr(state, "project_id", "")
        or getattr(state, "workspace_host_path", "")
        or "project"
    )


def has_explicit_confirmation(text: str) -> bool:
    return any(marker in text for marker in CONFIRM_MARKERS)


def rule_key(text: str) -> str:
    """Stable identity of one L3 rule text (normalized-content SHA1 prefix)."""
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def render_persona(entries: list[PersonaEntry]) -> str:
    """Render the merged persona view (manual first, then confirmed auto rules).

    Manual entries precede auto entries so a human-written rule is read before —
    and therefore wins over — an auto-distilled one (plan §3.1: 手写优先级更高).
    Auto *candidates* are never rendered: a candidate is unconfirmed advice, and
    the prompt track must not treat it as an established rule (plan §5).
    """
    if not entries:
        return ""
    lines = ["Persona (long-term, project-scoped):"]
    for entry in entries:
        if entry.origin != "manual" and entry.state != "confirmed":
            continue
        origin = "manual" if entry.origin == "manual" else "auto"
        if entry.profile.strip():
            lines.append(f"- profile[{origin}]: {entry.profile.strip()}")
        if entry.style.strip():
            lines.append(f"- style[{origin}]: {entry.style.strip()}")
        if entry.hard_rule.strip():
            lines.append(f"- hard_rule[{origin}]: {entry.hard_rule.strip()}")
    return "\n".join(lines)


class PersonaSynthesizer:
    """One ``json_mode`` LLM call → candidate L3 rules (never raises).

    ``synthesize`` returns ``None`` on provider or JSON failure so the escalation
    hook records ``persona_synthesis_failed`` and skips (plan §4.5); a valid but
    empty object returns ``[]`` (nothing worth keeping — not a failure).  Each
    non-empty facet becomes an independent candidate rule row with a stable
    ``rule_key`` so recurrence can confirm it later.
    """

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def synthesize(self, signals: list[L1Memory]) -> list[PersonaEntry] | None:
        if not signals:
            return []
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

    def _parse(self, text: str, *, signals: list[L1Memory]) -> list[PersonaEntry] | None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        confidence = _clamp_confidence(data.get("confidence", 0.5))
        source_ids = [m.record_id for m in signals if m.record_id is not None]
        entries: list[PersonaEntry] = []
        for field_name, value in (
            ("profile", _clean(data.get("profile"))),
            ("style", _clean(data.get("style"))),
            ("hard_rule", _clean(data.get("hard_rule"))),
        ):
            if not value:
                continue
            entry = PersonaEntry(
                rule_key=rule_key(value),
                source_record_ids=source_ids,
                origin="auto",
                confidence=confidence,
                state="candidate",
            )
            if field_name == "profile":
                entry.profile = value
            elif field_name == "style":
                entry.style = value
            else:
                entry.hard_rule = value
            entries.append(entry)
        return entries


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clamp_confidence(value: Any, *, default: float = 0.5) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    if confidence != confidence:  # NaN
        return default
    return max(0.0, min(1.0, confidence))


class PersonaEscalator:
    """The turn-end L3 escalation seam: synthesize candidates, confirm by proof.

    A pass fires when the persisted preference/constraint signal count reaches
    the threshold or the user emphasis marker appears.  Every synthesized rule
    becomes/updates a ``candidate`` row keyed by ``rule_key``; a rule is
    confirmed only by recurrence (re-synthesized on a later pass) or an explicit
    user confirmation — and a ``hard_rule`` additionally requires the explicit
    phrase, because safety-flavored rules must never self-confirm (plan §5).
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
        # The current turn's user message (stamped by the hook/processor) is
        # where an explicit confirmation lives; the session goal is only a
        # fallback so direct escalator tests keep working.
        goal = str(getattr(state, "_turn_user_message", "") or getattr(state, "goal", "") or "")
        signals = self._project_signals()
        if not signals:
            return
        new_signal_content = {
            memory.content
            for memory in new_memories
            if memory.type in PERSONA_SIGNAL_TYPES
        }
        emphasized = has_emphasis(goal)
        explicit = has_explicit_confirmation(goal)
        if new_memories and not new_signal_content and not emphasized and not explicit:
            return
        if len(signals) < self.persona_threshold and not emphasized and not explicit:
            return
        rules = self.synthesizer.synthesize(signals)
        if rules is None:
            metrics["persona_synthesis_failed"] = int(
                metrics.get("persona_synthesis_failed", 0)
            ) + 1
            self._record_generation(state, signals, [], status="failed", error="synthesis")
            _record_memory_event(
                state, "memory/l3_failed", {"reason": "synthesis_failed"}
            )
            return
        persona_ids: list[int] = []
        confirmed_count = 0
        for entry in rules:
            self._advance_rule_state(entry, explicit=explicit)
            try:
                persona_id = self.store.upsert_persona(entry)
            except Exception:  # noqa: BLE001 — degrade, never block the turn
                metrics["persona_synthesis_failed"] = int(
                    metrics.get("persona_synthesis_failed", 0)
                ) + 1
                continue
            persona_ids.append(persona_id)
            if entry.state == "confirmed":
                confirmed_count += 1
            _record_memory_event(
                state,
                "memory/l3_confirmed" if entry.state == "confirmed" else "memory/l3_candidate",
                {
                    "persona_id": persona_id,
                    "rule_key": entry.rule_key,
                    "rule_field": entry.rule_field(),
                    "confirmation_count": entry.confirmation_count,
                },
            )
        if not persona_ids:
            return
        self._record_generation(state, signals, persona_ids, status="completed")
        _record_memory_event(
            state,
            "memory/l3_upserted",
            {"persona_ids": persona_ids, "confirmed": confirmed_count},
        )
        metrics["persona_synthesized"] = int(metrics.get("persona_synthesized", 0)) + 1
        metrics["persona_signal_count"] = len(signals)
        metrics["persona_rules_confirmed"] = confirmed_count

    def _advance_rule_state(self, entry: PersonaEntry, *, explicit: bool) -> None:
        """Apply the candidate → confirmed state machine to one synthesized rule."""
        existing = self.store.persona_by_rule_key(entry.rule_key)
        is_hard = entry.rule_field() == "hard_rule"
        if existing is not None and existing.state == "confirmed":
            # Stay confirmed; refresh text/counters without demoting the rule.
            entry.state = "confirmed"
            entry.confirmation_count = existing.confirmation_count
            entry.confirmed_at = existing.confirmed_at
            return
        count = (existing.confirmation_count if existing is not None else 0) + 1
        entry.confirmation_count = count
        confirmed = explicit or (not is_hard and count >= L3_CONFIRM_THRESHOLD)
        entry.state = "confirmed" if confirmed else "candidate"
        entry.confirmed_at = _now() if confirmed else ""

    def _record_generation(
        self,
        state: Any,
        signals: list[L1Memory],
        persona_ids: list[int],
        *,
        status: str,
        error: str = "",
    ) -> None:
        try:
            self.store.record_generation(
                layer="L3",
                project_id=_project_id(state),
                source_run_id=str(getattr(state, "run_id", "") or ""),
                input_text="\n".join(
                    f"({m.type}, p{m.priority}) {m.content}" for m in signals
                ),
                output_text=json.dumps(persona_ids),
                status=status,
                record_ids=persona_ids,
                error=error,
            )
        except Exception:  # noqa: BLE001 — audit trail is best effort
            return

    def _project_signals(self) -> list[L1Memory]:
        try:
            memories = self.store.list_memories(scope="project")
        except Exception:  # noqa: BLE001 — degrade, no synthesis
            return []
        return [
            m
            for m in memories
            if m.type in PERSONA_SIGNAL_TYPES and m.status == "active"
        ]


def _persona_prompt(signals: list[L1Memory]) -> str:
    lines = [
        "Distill the following preference/constraint memories into long-term user rules.",
        'Return JSON: {"profile": "<who the user is>", "style": "<how they like to work>",',
        ' "hard_rule": "<non-negotiable constraints>", "confidence": 0.0-1.0}',
        "Use short phrases; each field may be empty if nothing applies.",
        "Memories:",
    ]
    for memory in signals:
        lines.append(f"- ({memory.type}, p{memory.priority}) {memory.content}")
    return "\n".join(lines)


# --- L2 scenario ------------------------------------------------------------


def topic_key(memory: L1Memory) -> str:
    """Return the persisted L2 topic bucket for one memory.

    Thin wrapper over :func:`derive_topic_key` so existing callers/tests keep
    the escalation-module name; the canonical priority (``source.topic`` >
    ``source.module`` > ``file:symbol`` > ``file`` > ``test_name`` >
    ``metadata.topic``) and normalization live in ``l1``.
    """
    return derive_topic_key(memory.source, memory.metadata)


def render_scenarios(entries: list[ScenarioEntry]) -> str:
    """Render L2 scenarios for the system cache track."""
    if not entries:
        return ""
    lines = ["Scenarios (long-term project knowledge):"]
    for entry in entries:
        lines.append(f"- scenario: {entry.scenario.strip()}")
        if entry.summary.strip():
            lines.append(f"  summary: {entry.summary.strip()}")
        steps = recipe_steps(entry)
        if steps:
            lines.append("  recipe:")
            for index, step in enumerate(steps, start=1):
                lines.append(f"    {index}. {step}")
    return "\n".join(lines)


class ScenarioSynthesizer:
    """One ``json_mode`` LLM call → a ``ScenarioEntry`` (never raises).

    The ``recipe`` is requested and stored as a JSON array of step strings so
    it can later be executed step-wise, not just read (plan §3);
    :func:`recipe_steps` still renders legacy plain-sentence rows.
    """

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
        steps = data.get("recipe")
        if isinstance(steps, str):
            steps = [steps]
        if not isinstance(steps, list):
            steps = []
        recipe = json.dumps(
            [str(step).strip() for step in steps if str(step).strip()],
            ensure_ascii=False,
        )
        if not summary and not recipe:
            return None
        return ScenarioEntry(
            scenario=scenario,
            summary=summary,
            recipe=recipe,
            source_record_ids=[m.record_id for m in memories if m.record_id is not None],
            topic_key=topic,
            confidence=_clamp_confidence(data.get("confidence", 0.5)),
            status="active",
            created_at=_now(),
        )


def _scenario_prompt(topic: str, memories: list[L1Memory]) -> str:
    lines = [
        f"Distill these memories (all about topic '{topic}') into one reusable scenario.",
        'Return JSON: {"scenario": "<short topic name>", "summary": "<what we know>",',
        ' "recipe": ["<step 1>", "<step 2>", ...], "confidence": 0.0-1.0}',
        "Use short phrases; a field may be empty if nothing applies.",
        "Memories:",
    ]
    for memory in memories:
        lines.append(f"- ({memory.type}) {memory.content}")
    return "\n".join(lines)


class ScenarioEscalator:
    """Turn-end L2 escalation over persisted ``topic_key`` clusters.

    Candidate topics are the clusters the store itself qualifies via
    :meth:`MemoryStore.topic_cluster_stats`: enough technical memories
    (``fact``/``decision``/``constraint``/``todo`` — a *preference* is an L3
    rule signal, never scenario material) drawn from at least
    ``MIN_SCENARIO_RUNS`` distinct runs, so one turn's repeated output cannot
    conjure a scenario (plan §5).  Only topics this turn actually touched (its
    stored/updated memories' ``topic_key``) are synthesized, upserted by
    ``topic_key`` with a rewritten ``scenario_members`` relation; each stage
    emits an audit event plus a generation row, and any failure records
    ``memory/l2_failed`` and leaves the threshold intact.
    """

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
        touched_topics = {
            memory.topic_key or derive_topic_key(memory.source, memory.metadata)
            for memory in new_memories
        }
        touched_topics.discard("")
        try:
            clusters = self.store.topic_cluster_stats(
                types=SCENARIO_TYPES,
                min_facts=self.scenario_threshold,
                min_runs=MIN_SCENARIO_RUNS,
            )
        except Exception:  # noqa: BLE001 — degrade, no synthesis
            return
        for topic in sorted(clusters):
            if topic not in touched_topics:
                # Avoid paying for a repeated synthesis when this turn added
                # nothing to the topic. A new fact causes an in-place refresh.
                continue
            members = self._members(topic)
            if not members:
                continue
            _record_memory_event(
                state,
                "memory/l2_candidate",
                {"topic_key": topic, **clusters[topic]},
            )
            entry = self.synthesizer.synthesize(topic, members)
            if entry is None:
                metrics["scenario_synthesis_failed"] = int(
                    metrics.get("scenario_synthesis_failed", 0)
                ) + 1
                self._record_generation(
                    state, topic, members, status="failed", error="synthesis_failed"
                )
                _record_memory_event(
                    state,
                    "memory/l2_failed",
                    {"topic_key": topic, "reason": "synthesis_failed"},
                )
                continue
            generation_id = self._record_generation(
                state, topic, members, status="completed", output_entry=entry
            )
            entry.generation_id = generation_id
            member_ids = [
                memory.record_id for memory in members if memory.record_id is not None
            ]
            try:
                scenario_id = self.store.upsert_scenario(
                    entry, member_record_ids=member_ids
                )
            except Exception:  # noqa: BLE001 — degrade, never block
                metrics["scenario_synthesis_failed"] = int(
                    metrics.get("scenario_synthesis_failed", 0)
                ) + 1
                _record_memory_event(
                    state,
                    "memory/l2_failed",
                    {"topic_key": topic, "reason": "store_failed"},
                )
                continue
            _record_memory_event(
                state,
                "memory/l2_upserted",
                {
                    "topic_key": topic,
                    "scenario_id": scenario_id,
                    "member_record_ids": member_ids,
                    "generation_id": generation_id,
                },
            )
            metrics["scenario_synthesized"] = int(
                metrics.get("scenario_synthesized", 0)
            ) + 1
        try:
            metrics["scenario_count"] = len(self.store.list_scenarios())
        except Exception:  # noqa: BLE001
            metrics["scenario_count"] = 0

    def _members(self, topic: str) -> list[L1Memory]:
        try:
            return self.store.active_memories_by_topic(
                topic, types=SCENARIO_TYPES, limit=MAX_SCENARIO_INPUTS
            )
        except Exception:  # noqa: BLE001 — degrade, skip topic
            return []

    def _record_generation(
        self,
        state: Any,
        topic: str,
        members: list[L1Memory],
        *,
        status: str,
        error: str = "",
        output_entry: ScenarioEntry | None = None,
    ) -> int | None:
        try:
            return self.store.record_generation(
                layer="L2",
                project_id=_project_id(state),
                source_run_id=str(getattr(state, "run_id", "") or ""),
                input_text=_scenario_prompt(topic, members),
                output_text=(
                    json.dumps(
                        {
                            "scenario": output_entry.scenario,
                            "summary": output_entry.summary,
                            "recipe": output_entry.recipe,
                        },
                        ensure_ascii=False,
                    )
                    if output_entry is not None
                    else topic
                ),
                status=status,
                record_ids=[
                    memory.record_id for memory in members if memory.record_id is not None
                ],
                error=error,
            )
        except Exception:  # noqa: BLE001 — audit trail is best effort
            return None


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
