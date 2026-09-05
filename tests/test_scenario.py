"""Deterministic tests for V5.1 P2: L2 scenario escalation + system-track injection."""

import json
from dataclasses import dataclass, field

from minicc.core.context import ContextBuilder
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.state import RunState
from minicc.memory.escalation import (
    EscalationHook,
    ScenarioEscalator,
    ScenarioSynthesizer,
    render_scenarios,
    topic_key,
)
from minicc.memory.l1 import L1Memory, MemoryStore, ScenarioEntry


def _fact(content: str, *, file: str, run_id: str = "run-1") -> L1Memory:
    return L1Memory(
        type="fact",
        content=content,
        priority=50,
        scope="project",
        source={"file": file, "run_id": run_id},
    )


def _memory(**overrides) -> L1Memory:
    values = dict(
        type="fact",
        content="the auth bug root cause is the token check",
        priority=50,
        scope="project",
        source={"file": "src/auth.py"},
    )
    values.update(overrides)
    return L1Memory(**values)


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
        return ModelResponse(
            text=self.replies.pop(0),
            raw={},
            usage=ModelUsage(),
            latency_ms=1,
        )


# --- store scenario ---------------------------------------------------------


def test_upsert_scenario_updates_in_place_by_topic_key(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()

    first = store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="old", recipe="run x", topic_key="src/auth.py")
    )
    second = store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="new", recipe="run y", topic_key="src/auth.py")
    )

    entries = store.list_scenarios()
    assert first == second  # re-synthesis updates by topic_key, not appends
    assert len(entries) == 1
    assert (entries[0].summary, entries[0].recipe) == ("new", "run y")


def test_list_scenarios_respects_limit(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    for index in range(3):
        store.upsert_scenario(ScenarioEntry(scenario=f"topic-{index}"))

    assert len(store.list_scenarios()) == 3
    assert len(store.list_scenarios(limit=2)) == 2


# --- topic + render ---------------------------------------------------------


def test_topic_key_uses_source_file() -> None:
    assert topic_key(_fact("x", file="src/auth.py")) == "src/auth.py"
    assert topic_key(L1Memory(type="fact", content="y", priority=1, scope="project")) == ""


def test_render_scenarios() -> None:
    block = render_scenarios(
        [
            ScenarioEntry(
                scenario="auth",
                summary="token check",
                recipe=json.dumps(["run tests", "check token"]),
            )
        ]
    )
    assert "Scenarios (long-term project knowledge):" in block
    assert "- scenario: auth" in block
    assert "summary: token check" in block
    assert "1. run tests" in block
    assert "2. check token" in block


# --- synthesizer ------------------------------------------------------------


def test_scenario_synthesizer_parses_valid_json() -> None:
    provider = ScriptedProvider(
        [
            json.dumps(
                {"scenario": "auth", "summary": "token bug", "recipe": ["run test_auth"]}
            )
        ]
    )
    entry = ScenarioSynthesizer(provider).synthesize("src/auth.py", [_memory()])
    assert entry is not None
    assert (entry.scenario, entry.summary) == ("auth", "token bug")
    assert json.loads(entry.recipe) == ["run test_auth"]
    assert entry.topic_key == "src/auth.py"


def test_scenario_synthesizer_defaults_scenario_to_topic() -> None:
    provider = ScriptedProvider(
        [json.dumps({"scenario": "", "summary": "s", "recipe": "r"})]
    )
    entry = ScenarioSynthesizer(provider).synthesize("src/auth.py", [_memory()])
    assert entry is not None
    assert entry.scenario == "src/auth.py"


def test_scenario_synthesizer_degrades_on_bad_json() -> None:
    entry = ScenarioSynthesizer(ScriptedProvider(["not json"])).synthesize(
        "src/auth.py", [_memory()]
    )
    assert entry is None


# --- escalator --------------------------------------------------------------


def test_scenario_escalator_skips_below_threshold(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_fact(f"login bug {index}", file="src/auth.py") for index in range(4)])

    provider = ScriptedProvider([])  # must never be called
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, [])
    assert provider.seen == []
    assert store.list_scenarios() == []


def _qualifying_facts(*, file: str = "src/auth.py", count: int = 5) -> list[L1Memory]:
    """Facts that satisfy both guards: threshold reached AND >= 2 distinct runs."""
    return [
        _fact(f"login bug {index}", file=file, run_id=f"run-{index % 2}")
        for index in range(count)
    ]


def test_scenario_escalator_triggers_at_threshold(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    facts = _qualifying_facts()
    store.add_memories(facts)
    provider = ScriptedProvider(
        [
            json.dumps(
                {"scenario": "auth", "summary": "token check", "recipe": ["run test_auth"]}
            )
        ]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, facts)
    entries = store.list_scenarios()
    assert len(entries) == 1
    assert entries[0].scenario == "auth"
    assert entries[0].topic_key == "src/auth.py"
    assert entries[0].source_record_ids  # provenance back at the L1 signals
    assert state.metrics["scenario_synthesized"] == 1
    assert state.metrics["scenario_count"] == 1


def test_l2_groups_by_topic_key(tmp_path) -> None:
    """Only the topic this turn touched is synthesized, not every qualifying one."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    auth = _qualifying_facts()
    billing = _qualifying_facts(file="src/billing.py")
    store.add_memories([*auth, *billing])
    provider = ScriptedProvider(
        [json.dumps({"scenario": "auth", "summary": "s", "recipe": ["r"]})]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, auth)  # only the auth topic was touched this turn
    entries = store.list_scenarios()
    assert len(entries) == 1
    assert entries[0].topic_key == "src/auth.py"


def test_l2_requires_multiple_runs(tmp_path) -> None:
    """Five facts from ONE run never conjure a scenario; a second run unlocks it."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    single_run = [_fact(f"login bug {index}", file="src/auth.py") for index in range(5)]
    store.add_memories(single_run)
    provider = ScriptedProvider(
        [json.dumps({"scenario": "auth", "summary": "s", "recipe": ["r"]})]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, single_run)
    assert provider.seen == []  # one run's repeated output is not a scenario
    assert store.list_scenarios() == []

    extra = store.add_memories([_fact("login bug again", file="src/auth.py", run_id="run-9")])
    refreshed = store.get_memory(extra[0])
    assert refreshed is not None
    escalator("s1", state, [refreshed])
    assert len(provider.seen) == 1
    assert len(store.list_scenarios()) == 1


def test_l2_same_topic_updates_in_place(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    facts = _qualifying_facts()
    store.add_memories(facts)
    provider = ScriptedProvider(
        [
            json.dumps({"scenario": "auth", "summary": "old", "recipe": ["r1"]}),
            json.dumps({"scenario": "auth", "summary": "new", "recipe": ["r2"]}),
        ]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, facts)
    escalator("s1", state, facts)
    entries = store.list_scenarios()
    assert len(entries) == 1
    assert (entries[0].summary, json.loads(entries[0].recipe)) == ("new", ["r2"])


def test_l2_scenario_members_are_persisted(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    facts = _qualifying_facts()
    record_ids = store.add_memories(facts)
    provider = ScriptedProvider(
        [json.dumps({"scenario": "auth", "summary": "s", "recipe": ["r"]})]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, facts)
    scenario = store.list_scenarios()[0]
    assert store.scenario_member_ids(scenario.scenario_id) == sorted(record_ids)
    # The relation is the real L1↔L2 membership and survives a read model copy.
    assert scenario.source_record_ids == sorted(record_ids)


def test_l2_unrelated_topics_do_not_merge(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    auth = _qualifying_facts()
    billing = _qualifying_facts(file="src/billing.py")
    store.add_memories([*auth, *billing])
    provider = ScriptedProvider(
        [
            json.dumps({"scenario": "auth", "summary": "s", "recipe": ["r"]}),
            json.dumps({"scenario": "billing", "summary": "s", "recipe": ["r"]}),
        ]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, [*auth, *billing])
    entries = store.list_scenarios()
    assert {entry.topic_key for entry in entries} == {"src/auth.py", "src/billing.py"}
    members_by_topic = {
        entry.topic_key: set(store.scenario_member_ids(entry.scenario_id))
        for entry in entries
    }
    assert members_by_topic["src/auth.py"].isdisjoint(members_by_topic["src/billing.py"])


def test_l2_excludes_preference_signals(tmp_path) -> None:
    """A preference is L3 material — it never lands in a scenario cluster."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    prefs = [
        L1Memory(
            type="preference",
            content=f"prefer {index}",
            priority=60,
            scope="project",
            source={"file": "src/auth.py", "run_id": f"run-{index}"},
        )
        for index in range(6)
    ]
    store.add_memories(prefs)
    provider = ScriptedProvider([])
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, prefs)
    assert provider.seen == []
    assert store.list_scenarios() == []


def test_scenario_escalator_clusters_by_file_not_total_count(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    # 3 in auth + 4 in billing = 7 total, but no single topic reaches 5.
    store.add_memories([_fact("a", file="src/auth.py") for _ in range(3)])
    store.add_memories([_fact("b", file="src/billing.py") for _ in range(4)])
    provider = ScriptedProvider([])
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, [])
    assert provider.seen == []
    assert store.list_scenarios() == []


def test_scenario_escalator_synthesis_failure_does_not_block(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    facts = _qualifying_facts()
    store.add_memories(facts)
    provider = ScriptedProvider(["garbage"])
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, facts)
    assert store.list_scenarios() == []
    assert state.metrics["scenario_synthesis_failed"] == 1


def test_escalation_hook_runs_persona_and_scenario(tmp_path) -> None:
    from minicc.memory.escalation import PersonaEscalator, PersonaSynthesizer

    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    preferences = [
        L1Memory(type="preference", content="prefer tabs", priority=60, scope="project"),
        L1Memory(type="preference", content="prefer python", priority=60, scope="project"),
        L1Memory(type="preference", content="prefer tests", priority=60, scope="project"),
    ]
    facts = [
        _fact("login bug", file="src/auth.py", run_id="run-1"),
        _fact("token expired", file="src/auth.py", run_id="run-2"),
    ]
    stored_ids = store.add_memories([*preferences, *facts])
    new_memories = [store.get_memory(record_id) for record_id in stored_ids]
    provider = ScriptedProvider(
        [
            json.dumps({"profile": "p", "style": "s", "hard_rule": ""}),  # persona
            json.dumps({"scenario": "auth", "summary": "x", "recipe": ["y"]}),  # scenario
        ]
    )
    hook = EscalationHook(
        persona=PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3),
        scenario=ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=1),
    )
    state = RunState.start("anything")

    hook("s1", state, [memory for memory in new_memories if memory is not None])
    assert len(store.list_persona()) == 2  # one candidate rule per non-empty facet
    assert len(store.list_scenarios()) == 1
    assert state.metrics["persona_synthesized"] == 1
    assert state.metrics["scenario_synthesized"] == 1


# --- context injection ------------------------------------------------------


def test_context_builder_injects_scenario_into_system_prefix(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="token check", recipe="run test_auth")
    )

    state = RunState.start("run a task")
    builder = ContextBuilder(memory_store=store)
    messages = builder.build_messages(state, [])

    assert messages[0]["role"] == "system"
    assert "Scenarios (long-term project knowledge):" in messages[0]["content"]
    assert "run test_auth" in messages[0]["content"]
    assert state.metrics["l2_scenarios_injected"] == 1


def test_context_builder_no_store_injects_no_scenario(tmp_path) -> None:
    state = RunState.start("run a task")
    builder = ContextBuilder()
    messages = builder.build_messages(state, [])
    assert "Scenarios (long-term project knowledge):" not in messages[0]["content"]
    assert state.metrics["l2_scenarios_injected"] == 0