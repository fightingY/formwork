"""Deterministic tests for V5.1 P3: LLM dedup (store/skip/update/merge)."""

import json
import sqlite3
from dataclasses import dataclass, field

from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.state import RunState
from minicc.memory.dedup import L1Deduper
from minicc.memory.l1 import L1Distiller, L1Memory, MemoryStore, MemoryTurnHook


def _memory(content: str, **overrides) -> L1Memory:
    values = dict(type="fact", content=content, priority=50, scope="project")
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


# --- deduper ----------------------------------------------------------------


def test_deduper_parses_decisions() -> None:
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {"index": 0, "action": "store", "record_id": None, "content": None},
                    {"index": 1, "action": "skip", "record_id": 7, "content": None},
                    {"index": 2, "action": "update", "record_id": 8, "content": "new text"},
                    {"index": 3, "action": "merge", "record_id": 8, "content": "merged"},
                ]
            )
        ]
    )
    decisions = L1Deduper(provider).dedup(
        [_memory("a"), _memory("b"), _memory("c"), _memory("d")],
        [_memory("existing")],
    )
    assert decisions is not None
    assert [(d.action, d.record_id) for d in decisions] == [
        ("store", None),
        ("skip", 7),
        ("update", 8),
        ("merge", 8),
    ]
    assert decisions[2].content == "new text"


def test_deduper_degrades_on_bad_json() -> None:
    assert L1Deduper(ScriptedProvider(["not json"])).dedup([_memory("a")], []) is None


def test_deduper_degrades_on_provider_error() -> None:
    class Broken:
        def complete(self, messages, *, options=None):
            raise RuntimeError("down")

    assert L1Deduper(Broken()).dedup([_memory("a")], []) is None


def test_deduper_drops_invalid_items() -> None:
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {"index": 0, "action": "store"},
                    {"index": 1, "action": "explode"},  # invalid action -> dropped
                    {"index": 2, "action": "skip", "record_id": 3},
                ]
            )
        ]
    )
    decisions = L1Deduper(provider).dedup([_memory("a"), _memory("b"), _memory("c")], [])
    assert decisions is not None
    assert [(d.index, d.action) for d in decisions] == [(0, "store"), (2, "skip")]


# --- store integration via the turn-end hook --------------------------------


def _hook(store: MemoryStore, provider: ScriptedProvider) -> MemoryTurnHook:
    return MemoryTurnHook(
        store,
        L1Distiller(ScriptedProvider([])),
        deduper=L1Deduper(provider),
    )


def test_dedup_store_action_inserts(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    state = RunState.start("anything")
    hook = _hook(store, ScriptedProvider([json.dumps([{"index": 0, "action": "store"}])]))

    stored = hook.projector.store_with_dedup(state, [_memory("brand new fact")])
    assert len(stored) == 1
    assert store.count_memories() == 1
    assert state.metrics["memory_dedup_store"] == 1


def test_dedup_skip_action_drops(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory("the auth bug root cause is the token check")])
    state = RunState.start("anything")
    hook = _hook(
        store,
        ScriptedProvider([json.dumps([{"index": 0, "action": "skip", "record_id": 1}])]),
    )

    stored = hook.projector.store_with_dedup(
        state, [_memory("the auth bug root cause is the token check")]
    )
    assert stored == []
    assert store.count_memories() == 1  # nothing added
    assert state.metrics["memory_dedup_skip"] == 1


def test_dedup_update_action_replaces_content(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory("the auth bug root cause is the token check")])
    state = RunState.start("anything")
    hook = _hook(
        store,
        ScriptedProvider(
            [
                json.dumps(
                    [
                        {
                            "index": 0,
                            "action": "update",
                            "record_id": 1,
                            "content": "the auth bug root cause is the session cookie",
                        }
                    ]
                )
            ]
        ),
    )

    stored = hook.projector.store_with_dedup(state, [_memory("the auth bug root cause is old")])
    assert [memory.record_id for memory in stored] == [1]
    assert store.count_memories() == 1
    assert store.list_memories()[0].content == "the auth bug root cause is the session cookie"
    assert state.metrics["memory_dedup_update"] == 1


def test_dedup_merge_action_replaces_content(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory("auth bug: token check")])
    state = RunState.start("anything")
    hook = _hook(
        store,
        ScriptedProvider(
            [
                json.dumps(
                    [
                        {
                            "index": 0,
                            "action": "merge",
                            "record_id": 1,
                            "content": "auth bug: token check + session cookie",
                        }
                    ]
                )
            ]
        ),
    )

    stored = hook.projector.store_with_dedup(state, [_memory("auth bug: session cookie")])
    assert [memory.record_id for memory in stored] == [1]
    assert store.count_memories() == 1
    assert "session cookie" in store.list_memories()[0].content
    assert state.metrics["memory_dedup_merge"] == 1


def test_dedup_failure_appends_all(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    state = RunState.start("anything")
    hook = _hook(store, ScriptedProvider(["not json"]))

    stored = hook.projector.store_with_dedup(state, [_memory("a new fact")])
    assert len(stored) == 1
    assert store.count_memories() == 1  # appended anyway (plan §4.5)
    assert state.metrics["memory_dedup_failed"] == 1


def test_dedup_missing_decision_defaults_to_store(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    state = RunState.start("anything")
    # The model returns an empty array -> every memory defaults to store.
    hook = _hook(store, ScriptedProvider([json.dumps([])]))

    stored = hook.projector.store_with_dedup(state, [_memory("a"), _memory("b")])
    assert len(stored) == 2
    assert store.count_memories() == 2


# --- generation audit (spec §7: DEDUP calls land in memory_generations) ------


def _dedup_generations(store: MemoryStore) -> list[sqlite3.Row]:
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM memory_generations WHERE layer='DEDUP' ORDER BY generation_id"
        ).fetchall()


def test_dedup_success_records_generation(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory("the auth bug root cause is the token check")])
    state = RunState.start("anything")
    state.run_id = "run-9"
    hook = _hook(
        store,
        ScriptedProvider(
            [
                json.dumps(
                    [{"index": 0, "action": "update", "record_id": 1, "content": "v2"}]
                )
            ]
        ),
    )

    stored = hook.projector.store_with_dedup(state, [_memory("the auth bug changed")])
    assert [memory.record_id for memory in stored] == [1]

    rows = _dedup_generations(store)
    assert len(rows) == 1  # one audit row per dedup call
    assert rows[0]["status"] == "completed"
    assert rows[0]["source_run_id"] == "run-9"
    assert json.loads(rows[0]["record_ids_json"]) == [1]
    # Hashes only: the stored output_hash is the sha256 of the decision list.
    assert len(rows[0]["output_hash"]) == 64


def test_dedup_failure_records_failed_generation(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    state = RunState.start("anything")
    hook = _hook(store, ScriptedProvider(["not json"]))

    stored = hook.projector.store_with_dedup(state, [_memory("a new fact")])
    assert len(stored) == 1  # append_all fallback still ran

    rows = _dedup_generations(store)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "dedup_unavailable"