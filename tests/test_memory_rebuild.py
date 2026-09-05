"""Deterministic tests for memory rebuild (spec §10: EventLog 不可变，读模型可重建)."""

import json
import sqlite3
from dataclasses import dataclass, field

from minicc.core.events import EventLog
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.memory.escalation import (
    EscalationHook,
    PersonaEscalator,
    PersonaSynthesizer,
    ScenarioEscalator,
    ScenarioSynthesizer,
)
from minicc.memory.l1 import (
    L1Distiller,
    L1Memory,
    MemoryStore,
    PersonaEntry,
    ScenarioEntry,
)
from minicc.memory.rebuild import rebuild_from_event_log


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


def _fact(content: str, *, file: str, run_id: str = "run-1") -> L1Memory:
    return L1Memory(
        type="fact",
        content=content,
        priority=50,
        scope="project",
        source={"file": file, "run_id": run_id},
    )


def _preference(content: str) -> L1Memory:
    return L1Memory(
        type="preference", content=content, priority=60, scope="project"
    )


def _write_session_log(path, *, run_id: str = "run-1") -> EventLog:
    """One completed turn: user ask → tool run → final answer."""
    log = EventLog(path)
    log.append("turn/start", {"turn": 1, "run_id": run_id})
    log.append("user/message", {"turn": 1, "content": "how do we deploy auth?"})
    log.append(
        "tool/call",
        {"turn": 1, "step": 1, "call_id": "c1", "tool": "bash",
         "arguments": {"command": "make deploy-auth"}},
    )
    log.append(
        "tool/result",
        {"turn": 1, "step": 1, "call_id": "c1", "tool": "bash", "is_error": False,
         "content": "deployed"},
    )
    log.append(
        "assistant/message",
        {"turn": 1, "message": {"content": "deploy auth with make deploy-auth"}},
    )
    log.append("turn/end", {"turn": 1})
    return log


def test_rebuild_does_not_modify_event_log(tmp_path) -> None:
    """A rebuild is a read-model operation: L0 stays byte-identical, and no
    memory/... lifecycle events are re-appended to it (spec §10)."""
    log_path = tmp_path / "events.jsonl"
    _write_session_log(log_path)
    before = log_path.read_bytes()

    store = MemoryStore(tmp_path / "memory" / "project.db")
    provider = ScriptedProvider(
        [json.dumps([{"type": "fact", "content": "x", "scope": "project"}])]
    )
    manifest = rebuild_from_event_log(
        EventLog(log_path), store, L1Distiller(provider), project_id="proj"
    )

    assert log_path.read_bytes() == before
    types = [event.type for event in EventLog(log_path).events]
    assert not any(event_type.startswith("memory/") for event_type in types)
    assert manifest["event_count"] == 6
    assert manifest["turn_count"] == 1


def test_rebuild_recreates_l1(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    _write_session_log(log_path)
    store = MemoryStore(tmp_path / "memory" / "project.db")
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {
                        "type": "decision",
                        "content": "deploy auth with make deploy-auth",
                        "scope": "project",
                        "priority": 80,
                    }
                ]
            )
        ]
    )

    manifest = rebuild_from_event_log(
        EventLog(log_path), store, L1Distiller(provider), project_id="proj"
    )

    assert [memory.content for memory in store.list_memories()] == [
        "deploy auth with make deploy-auth"
    ]
    assert manifest["l1_count"] == 1
    assert manifest["mode"] == "semantic"
    assert manifest["snapshot_id"] is not None


def test_rebuild_recreates_l2_l3(tmp_path) -> None:
    """Semantic rebuild escalates too: seeded L1 signals re-project into L2/L3."""
    log_path = tmp_path / "events.jsonl"
    _write_session_log(log_path)
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    # Seed the L1 signal pool the escalators cluster (reset=False keeps it).
    store.add_memories(
        [
            *_fact_n("auth bug", 3),
            *_qualifying_facts(),
            *[_preference(f"prefer {index}") for index in range(3)],
        ]
    )
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {"type": "fact", "content": "touch auth", "scope": "project",
                     "source": {"file": "src/auth.py", "run_id": "run-1"}},
                    {"type": "preference", "content": "prefer short diffs",
                     "scope": "project"},
                ]
            ),
            json.dumps({"profile": "tidy", "style": "", "hard_rule": ""}),
            json.dumps({"scenario": "auth", "summary": "token bug", "recipe": ["r"]}),
        ]
    )
    escalator = EscalationHook(
        persona=PersonaEscalator(
            store, PersonaSynthesizer(provider), persona_threshold=3
        ),
        scenario=ScenarioEscalator(
            store, ScenarioSynthesizer(provider), scenario_threshold=5
        ),
    )

    manifest = rebuild_from_event_log(
        EventLog(log_path),
        store,
        L1Distiller(provider),
        project_id="proj",
        reset=False,
        escalator=escalator,
    )

    assert manifest["l2_count"] == 1
    assert manifest["l3_candidate_count"] >= 1
    assert len(store.list_scenarios()) == 1
    assert len(store.list_persona(state="candidate")) >= 1


def _fact_n(prefix: str, count: int) -> list[L1Memory]:
    return [_fact(f"{prefix} {index}", file="src/auth.py") for index in range(count)]


def _qualifying_facts() -> list[L1Memory]:
    return [
        _fact(f"login bug {index}", file="src/auth.py", run_id=f"run-{index % 2}")
        for index in range(5)
    ]


def test_rebuild_publishes_snapshot(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    _write_session_log(log_path)
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="token bug", topic_key="src/auth.py")
    )
    store.upsert_persona(
        PersonaEntry(style="tests first", origin="auto", state="confirmed")
    )
    provider = ScriptedProvider(
        [json.dumps([{"type": "fact", "content": "x", "scope": "project"}])]
    )

    manifest = rebuild_from_event_log(
        EventLog(log_path), store, L1Distiller(provider), project_id="proj", reset=False
    )

    snapshot_id = store.active_snapshot_id(project_id="proj")
    assert manifest["snapshot_id"] == snapshot_id
    assert snapshot_id is not None
    with sqlite3.connect(str(store.db_path)) as conn:
        layers = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT layer FROM memory_snapshot_items WHERE snapshot_id=?",
                (snapshot_id,),
            )
        }
    assert layers <= {"l2", "l3"}
    assert [s.scenario for s in store.list_snapshot_scenarios(snapshot_id)] == ["auth"]
    assert [p.style for p in store.list_snapshot_persona(snapshot_id)] == ["tests first"]


# --- deterministic track (no LLM) --------------------------------------------


def test_deterministic_rebuild_needs_no_model(tmp_path) -> None:
    """mode=deterministic repairs the read model with zero provider calls."""
    log_path = tmp_path / "events.jsonl"
    _write_session_log(log_path)
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    # A row whose provenance carries a file anchor but whose topic_key is empty
    # (e.g. written by an older version): deterministic repair backfills it.
    store.add_memories([_fact("deploy auth with make deploy-auth", file="src/auth.py")])
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute("UPDATE memories SET topic_key=''")
        conn.execute("DELETE FROM memories_fts")  # index lost; rebuild must fix it
        conn.commit()

    provider = ScriptedProvider([])  # must never be called
    manifest = rebuild_from_event_log(
        EventLog(log_path),
        store,
        L1Distiller(provider),
        project_id="proj",
        mode="deterministic",
    )

    assert provider.seen == []
    assert manifest["mode"] == "deterministic"
    assert manifest["reset"] is False
    assert manifest["l1_count"] == 1  # rows are repaired in place, not re-created
    assert manifest["repair"]["topic_keys_backfilled"] == 1
    assert manifest["repair"]["fts_rows"] == 1
    # The FTS index works again and the topic key came back from provenance.
    assert [m.content for m in store.search("deploy auth")] == [
        "deploy auth with make deploy-auth"
    ]
    assert store.list_memories()[0].topic_key == "src/auth.py"


def test_unknown_rebuild_mode_is_rejected(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    try:
        rebuild_from_event_log(
            EventLog(tmp_path / "events.jsonl"),
            store,
            L1Distiller(ScriptedProvider([])),
            mode="nonsense",
        )
    except ValueError as exc:
        assert "nonsense" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
