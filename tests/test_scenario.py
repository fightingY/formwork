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


def _fact(content: str, *, file: str) -> L1Memory:
    return L1Memory(
        type="fact",
        content=content,
        priority=50,
        scope="project",
        source={"file": file},
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


def test_upsert_scenario_updates_in_place_by_name(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()

    first = store.upsert_scenario(ScenarioEntry(scenario="auth", summary="old", recipe="run x"))
    second = store.upsert_scenario(ScenarioEntry(scenario="auth", summary="new", recipe="run y"))

    entries = store.list_scenarios()
    assert first == second  # re-synthesis updates, not appends (plan §3.1)
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
        [ScenarioEntry(scenario="auth", summary="token check", recipe="run tests")]
    )
    assert "Scenarios (long-term project knowledge):" in block
    assert "- scenario: auth" in block
    assert "summary: token check" in block
    assert "recipe: run tests" in block


# --- synthesizer ------------------------------------------------------------


def test_scenario_synthesizer_parses_valid_json() -> None:
    provider = ScriptedProvider(
        [json.dumps({"scenario": "auth", "summary": "token bug", "recipe": "run test_auth"})]
    )
    entry = ScenarioSynthesizer(provider).synthesize("src/auth.py", [_memory()])
    assert entry is not None
    assert (entry.scenario, entry.summary, entry.recipe) == (
        "auth",
        "token bug",
        "run test_auth",
    )


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


def test_scenario_escalator_triggers_at_threshold(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_fact(f"login bug {index}", file="src/auth.py") for index in range(5)])
    provider = ScriptedProvider(
        [json.dumps({"scenario": "auth", "summary": "token check", "recipe": "run test_auth"})]
    )
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, [])
    entries = store.list_scenarios()
    assert len(entries) == 1
    assert entries[0].scenario == "auth"
    assert entries[0].source_record_ids  # provenance back at the L1 signals
    assert state.metrics["scenario_synthesized"] == 1
    assert state.metrics["scenario_count"] == 1


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
    store.add_memories([_fact("bug", file="src/auth.py") for _ in range(5)])
    provider = ScriptedProvider(["garbage"])
    escalator = ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=5)
    state = RunState.start("anything")

    escalator("s1", state, [])
    assert store.list_scenarios() == []
    assert state.metrics["scenario_synthesis_failed"] == 1


def test_escalation_hook_runs_persona_and_scenario(tmp_path) -> None:
    from minicc.memory.escalation import PersonaEscalator, PersonaSynthesizer

    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [
            L1Memory(type="preference", content="prefer tabs", priority=60, scope="project"),
            L1Memory(type="preference", content="prefer python", priority=60, scope="project"),
            L1Memory(type="preference", content="prefer tests", priority=60, scope="project"),
            _fact("login bug", file="src/auth.py"),
        ]
    )
    provider = ScriptedProvider(
        [
            json.dumps({"profile": "p", "style": "s", "hard_rule": ""}),  # persona
            json.dumps({"scenario": "auth", "summary": "x", "recipe": "y"}),  # scenario
        ]
    )
    hook = EscalationHook(
        persona=PersonaEscalator(store, PersonaSynthesizer(provider), persona_threshold=3),
        scenario=ScenarioEscalator(store, ScenarioSynthesizer(provider), scenario_threshold=1),
    )
    state = RunState.start("anything")

    hook("s1", state, [])
    assert len(store.list_persona()) == 1
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