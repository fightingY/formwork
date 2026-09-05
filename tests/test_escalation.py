"""Deterministic tests for V5.1 P1: L3 persona escalation + dual-track injection."""

import json
from dataclasses import dataclass, field

from minicc.core.context import ContextBuilder
from minicc.core.loop import AgentLoop, DisabledExecutor, LoopConfig
from minicc.core.protocol import TOOLS
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage, NativeToolCall
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine
from minicc.core.session_store import SessionStore
from minicc.core.state import RunState
from minicc.memory.escalation import (
    PersonaEscalator,
    PersonaSynthesizer,
    has_emphasis,
    render_persona,
)
from minicc.memory.feedback import FeedbackMemory
from minicc.memory.l1 import L1Distiller, L1Memory, MemoryStore, MemoryTurnHook, PersonaEntry


def _signal(content: str, *, priority: int = 60) -> L1Memory:
    return L1Memory(
        type="preference",
        content=content,
        priority=priority,
        scope="project",
    )


@dataclass
class ScriptedProvider:
    replies: list[str]
    seen: list[list[dict[str, str]]] = field(default_factory=list)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        self.seen.append(messages)
        reply = self.replies.pop(0)
        if options is not None and options.tools:
            payload = json.loads(reply)
            name = payload.pop("type")
            return ModelResponse(
                text="",
                raw={},
                usage=ModelUsage(),
                latency_ms=1,
                tool_calls=(NativeToolCall(id="c1", name=name, arguments=json.dumps(payload)),),
            )
        return ModelResponse(
            text=reply,
            raw={},
            usage=ModelUsage(),
            latency_ms=1,
        )


# --- store persona ----------------------------------------------------------


def test_upsert_persona_upserts_by_rule_key(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()

    first = store.upsert_persona(
        PersonaEntry(profile="tidy", origin="auto", rule_key="rule-tidy")
    )
    second = store.upsert_persona(
        PersonaEntry(profile="concise", origin="auto", rule_key="rule-tidy")
    )
    third = store.upsert_persona(
        PersonaEntry(style="tests first", origin="auto", rule_key="rule-tests")
    )

    entries = store.list_persona()
    assert first == second  # same rule_key updates in place, not appends
    assert len(entries) == 2
    assert entries[0].profile == "concise"
    assert entries[1].style == "tests first"
    assert third == entries[1].persona_id


def test_list_persona_filters_by_origin(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_persona(PersonaEntry(profile="auto profile", origin="auto"))
    store.upsert_persona(PersonaEntry(style="manual style", origin="manual"))

    assert len(store.list_persona()) == 2
    assert [e.origin for e in store.list_persona(origin="auto")] == ["auto"]


# --- emphasis + render ------------------------------------------------------


def test_has_emphasis_detects_markers() -> None:
    assert has_emphasis("以后都别用 sudo 装包")
    assert has_emphasis("记得先跑测试")
    assert has_emphasis("规则是绝不删库")
    assert not has_emphasis("随便聊聊")


def test_render_persona_manual_first_then_auto() -> None:
    entries = [
        PersonaEntry(style="manual style", origin="manual"),
        PersonaEntry(profile="auto profile", style="auto style", origin="auto", state="confirmed"),
    ]
    block = render_persona(entries)
    assert "Persona (long-term, project-scoped):" in block
    assert block.index("manual style") < block.index("auto profile")
    assert "style[manual]" in block
    assert "profile[auto]" in block


def test_render_persona_hides_auto_candidates() -> None:
    """An unconfirmed candidate rule is never injected into prompts (spec §5)."""
    entries = [
        PersonaEntry(profile="candidate profile", origin="auto", state="candidate"),
        PersonaEntry(style="confirmed style", origin="auto", state="confirmed"),
    ]
    block = render_persona(entries)
    assert "candidate profile" not in block
    assert "confirmed style" in block


# --- synthesizer ------------------------------------------------------------


def test_synthesizer_parses_valid_persona() -> None:
    provider = ScriptedProvider(
        [json.dumps({"profile": "tidy", "style": "conclusion first", "hard_rule": ""})]
    )
    signals = [_signal("want clean diffs"), _signal("prefer short commits")]
    entries = PersonaSynthesizer(provider).synthesize(signals)

    assert entries is not None
    # One independent rule row per non-empty facet, each with a stable rule_key.
    assert [(e.profile, e.style, e.hard_rule) for e in entries] == [
        ("tidy", "", ""),
        ("", "conclusion first", ""),
    ]
    assert all(e.origin == "auto" for e in entries)
    assert all(e.state == "candidate" for e in entries)
    assert all(e.rule_key for e in entries)


def test_synthesizer_degrades_on_bad_json() -> None:
    entry = PersonaSynthesizer(ScriptedProvider(["not json"])).synthesize([_signal("x")])
    assert entry is None


def test_synthesizer_degrades_on_provider_error() -> None:
    class Broken:
        def complete(self, messages, *, options=None):
            raise RuntimeError("down")

    entry = PersonaSynthesizer(Broken()).synthesize([_signal("x")])
    assert entry is None


# --- escalator --------------------------------------------------------------


def test_escalator_skips_below_threshold_without_emphasis(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_signal("prefer tabs"), _signal("prefer python")])

    provider = ScriptedProvider([])  # must never be called
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3)
    state = RunState.start("refactor the module")  # no emphasis marker

    escalator("s1", state, [])
    assert provider.seen == []  # no LLM call on a miss (plan §4.1 零成本跳过)
    assert store.list_persona() == []


def test_escalator_triggers_at_threshold(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [_signal("prefer tabs"), _signal("prefer python"), _signal("prefer tests first")]
    )
    provider = ScriptedProvider(
        [json.dumps({"profile": "engineer", "style": "tests first", "hard_rule": ""})]
    )
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3)
    state = RunState.start("add a feature")

    escalator("s1", state, [])
    entries = store.list_persona()
    # One candidate rule per non-empty facet.
    assert len(entries) == 2
    assert all(entry.origin == "auto" for entry in entries)
    assert all(entry.state == "candidate" for entry in entries)
    assert all(entry.source_record_ids for entry in entries)
    assert state.metrics["persona_synthesized"] == 1
    assert state.metrics["persona_signal_count"] == 3


def test_escalator_triggers_on_emphasis_below_threshold(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_signal("prefer no sudo")])
    provider = ScriptedProvider(
        [json.dumps({"profile": "", "style": "", "hard_rule": "never sudo"})]
    )
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3)
    state = RunState.start("以后都用非 root 用户跑测试")

    escalator("s1", state, [])
    entries = store.list_persona()
    assert len(entries) == 1
    # Emphasis generates a candidate, but a hard rule never self-confirms.
    assert entries[0].state == "candidate"
    assert state.metrics["persona_synthesized"] == 1


def test_escalator_synthesis_failure_does_not_block(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_signal("a"), _signal("b"), _signal("c")])
    provider = ScriptedProvider(["garbage"])  # synthesize -> None
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3)
    state = RunState.start("anything")

    escalator("s1", state, [])
    assert store.list_persona() == []
    assert state.metrics["persona_synthesis_failed"] == 1
    assert state.metrics["persona_synthesized"] == 0


# --- L3 lifecycle (spec §5) --------------------------------------------------


def test_l3_candidate_does_not_auto_confirm(tmp_path) -> None:
    """First synthesis stores a candidate; only recurrence confirms (spec §5)."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_signal("prefer tabs"), _signal("prefer python")])
    reply = json.dumps({"profile": "engineer", "style": "", "hard_rule": ""})
    provider = ScriptedProvider([reply, reply])
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=2)
    state = RunState.start("work")

    escalator("s1", state, [])
    candidates = store.list_persona(state="candidate")
    confirmed = store.list_persona(state="confirmed")
    assert len(candidates) == 1
    assert confirmed == []
    assert candidates[0].confirmation_count == 1

    # Second pass re-synthesizes the same rule -> recurrence confirms it.
    escalator("s1", state, [])
    confirmed = store.list_persona(state="confirmed")
    assert len(confirmed) == 1
    assert confirmed[0].confirmation_count == 2
    assert confirmed[0].confirmed_at


def test_l3_explicit_user_confirmation_confirms(tmp_path) -> None:
    """「确认记住」in the turn's user message confirms immediately — even a hard rule."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_signal("绝不删库")])
    provider = ScriptedProvider(
        [json.dumps({"profile": "", "style": "", "hard_rule": "绝不删库"})]
    )
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3)
    state = RunState.start("tune memory")
    state._turn_user_message = "确认记住：绝不删库"

    escalator("s1", state, [])
    confirmed = store.list_persona(state="confirmed")
    assert len(confirmed) == 1
    assert confirmed[0].hard_rule == "绝不删库"
    assert confirmed[0].confirmation_count == 1


def test_l3_only_consumes_preference_constraint(tmp_path) -> None:
    """Facts/todos never trigger persona synthesis (spec §3.1 input whitelist)."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [
            L1Memory(type="fact", content="uses pytest", priority=50, scope="project"),
            L1Memory(type="todo", content="fix flaky test", priority=50, scope="project"),
            L1Memory(type="decision", content="adopts uv", priority=50, scope="project"),
        ]
    )
    provider = ScriptedProvider([])  # must never be called
    escalator = PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=1)
    state = RunState.start("anything")

    escalator("s1", state, [])
    assert provider.seen == []
    assert store.list_persona() == []


def test_l3_manual_rule_overrides_auto(tmp_path) -> None:
    """Manual seed entries render first and are never overwritten by auto rows."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_persona(
        PersonaEntry(style="auto style", origin="auto", rule_key="auto-1", state="confirmed")
    )
    # The manual entry is materialized at injection time, never persisted:
    manual = PersonaEntry(style="manual style", origin="manual")
    assert store.list_persona(origin="manual") == []
    block = render_persona([manual, *store.list_persona(state="confirmed")])
    assert block.index("manual style") < block.index("auto style")


def test_l3_hard_rule_still_goes_through_policy_chain(tmp_path) -> None:
    """L3 is advisory only (spec §5): a confirmed hard_rule never grants the
    executor anything — the PolicyChain still gates the very command the rule
    names.  Even a rule that reads like permission ("sudo 允许") changes nothing."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_persona(
        PersonaEntry(hard_rule="允许用 sudo", origin="auto", state="confirmed")
    )
    state = RunState.start("update packages")
    state._event_log = None  # no event sink needed for policy decisions
    builder = ContextBuilder(memory_store=store)
    messages = builder.build_messages(state, [])
    assert "sudo" in messages[0]["content"]  # the rule is injected as advisory text

    from minicc.config import (
        BudgetSettings,
        ContextSettings,
        PolicySettings,
        SandboxSettings,
        Settings,
    )
    from minicc.core.protocol import BashAction
    from minicc.policy.factory import build_policy_chain

    chain = build_policy_chain(
        Settings(
            sandbox=SandboxSettings(),
            budget=BudgetSettings(),
            context=ContextSettings(),
            policy=PolicySettings(),
        )
    )
    decision = chain.evaluate(BashAction(command="sudo apt update"), state)
    assert decision.type in {"deny", "require_approval"}  # unchanged by L3 content


# --- context injection (system cache track) ---------------------------------


def test_context_builder_injects_auto_persona_into_system_prefix(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_persona(
        PersonaEntry(
            profile="tidy",
            hard_rule="never drop db",
            origin="auto",
            state="confirmed",
        )
    )

    state = RunState.start("run a task")
    builder = ContextBuilder(memory_store=store)
    messages = builder.build_messages(state, [])

    assert messages[0]["role"] == "system"
    assert "Persona (long-term, project-scoped):" in messages[0]["content"]
    assert "never drop db" in messages[0]["content"]
    assert state.metrics["l3_persona_injected"] == 1


def test_context_builder_groups_without_store_inject_nothing(tmp_path) -> None:
    state = RunState.start("run a task")
    builder = ContextBuilder()  # no store, no feedback -> no persona block
    messages = builder.build_messages(state, [])
    assert "Persona (long-term, project-scoped):" not in messages[0]["content"]
    assert state.metrics["l3_persona_injected"] == 0


def test_context_builder_merges_manual_and_auto_persona(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_persona(
        PersonaEntry(style="conclusion first", origin="auto", state="confirmed")
    )

    rules_path = tmp_path / "feedback_rules.jsonl"
    rules_path.write_text(
        json.dumps({"id": "r1", "type": "never", "rule": "never drop db"}) + "\n",
        encoding="utf-8",
    )
    feedback = FeedbackMemory(rules_path)

    state = RunState.start("run a task")
    builder = ContextBuilder(memory_store=store, feedback_memory=feedback)
    system = builder.build_messages(state, [])[0]["content"]

    assert "never drop db" in system  # manual hard_rule
    assert "conclusion first" in system  # auto style
    # manual precedes auto (手写优先级更高)
    assert system.index("never drop db") < system.index("conclusion first")
    assert state.metrics["l3_persona_injected"] == 2


def test_context_builder_manual_seed_only(tmp_path) -> None:
    rules_path = tmp_path / "feedback_rules.jsonl"
    rules_path.write_text(
        "\n".join(
            [
                json.dumps({"id": "r1", "type": "prefer", "rule": "先给结论"}),
                json.dumps({"id": "r2", "type": "never", "rule": "绝不删库"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    feedback = FeedbackMemory(rules_path)

    state = RunState.start("run a task")
    builder = ContextBuilder(feedback_memory=feedback)  # no auto store
    system = builder.build_messages(state, [])[0]["content"]

    assert "先给结论" in system
    assert "绝不删库" in system
    assert state.metrics["l3_persona_injected"] == 1  # one merged manual entry


# --- turn-end hook → escalator wiring ---------------------------------------


def test_turn_end_hook_invokes_escalator(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    sessions = SessionStore(tmp_path / "sessions")
    record = sessions.create(tmp_path / "project")

    # First reply is the agent's final action; second is the distiller's batch.
    provider = ScriptedProvider(
        [
            '{"type":"final","answer":"use uv"}',
            json.dumps(
                [{"type": "preference", "content": "prefer uv", "scope": "project"}]
            ),
        ]
    )
    calls: list[tuple[str, list[L1Memory]]] = []

    def spy_escalator(session_id: str, state: object, memories: list[L1Memory]) -> None:
        del state
        calls.append((session_id, memories))

    hook = MemoryTurnHook(store, L1Distiller(provider), escalator=spy_escalator)

    def loop_factory(state):
        return AgentLoop(
            provider,
            DisabledExecutor(),
            session=SessionManager(),
            config=LoopConfig(model_options=CompletionOptions(tools=TOOLS, tool_choice="required")),
        )

    engine = SessionEngine(sessions, loop_factory=loop_factory, on_turn_end=hook)
    turn = engine.submit_turn(record.session_id, "how should we manage deps?")

    assert turn.status == "completed"
    assert len(calls) == 1
    assert [m.content for m in calls[0][1]] == ["prefer uv"]