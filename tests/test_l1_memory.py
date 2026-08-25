import json
import sqlite3
from dataclasses import dataclass, field

from minicc.core.context import ContextBuilder
from minicc.core.loop import AgentLoop, DisabledExecutor
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine
from minicc.core.session_store import SessionStore
from minicc.core.state import RunState
from minicc.memory.l1 import (
    L1Distiller,
    L1Memory,
    MemoryStore,
    MemoryTurnHook,
    format_relevant_memories,
    project_db_path,
    recall_memories,
)


def _memory(**overrides) -> L1Memory:
    values = dict(
        type="fact",
        content="the auth service deploy command is make deploy-auth",
        priority=80,
        scope="project",
        session_id="",
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


# --- store -------------------------------------------------------------------


def test_store_creates_all_four_tables(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()

    with sqlite3.connect(str(store.db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
            )
        }
    # P0 creates the whole L0→L3 schema up front (plan §4.3), fills only L1.
    assert {"memories", "memories_fts", "scenarios", "persona"} <= tables


def test_store_add_and_bm25_search_roundtrip(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    ids = store.add_memories(
        [
            _memory(content="deploy the auth service with make deploy-auth"),
            _memory(content="the billing module uses postgres for persistence"),
        ]
    )
    assert len(ids) == 2

    results = store.search("auth service deploy", scope="project", limit=5)
    assert [memory.content for memory in results] == [
        "deploy the auth service with make deploy-auth"
    ]
    assert store.count_memories() == 2


def test_store_search_respects_scope(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [
            _memory(content="decision to use uv for dependency management", scope="session"),
            _memory(content="the auth service deploy command is make deploy-auth"),
        ]
    )

    project_results = store.search("auth deploy", scope="project")
    session_results = store.search("uv dependency management", scope="session")

    assert [m.content for m in project_results] == [
        "the auth service deploy command is make deploy-auth"
    ]
    assert [m.content for m in session_results] == [
        "decision to use uv for dependency management"
    ]
    # session-scoped memories never leak into project recall.
    assert all(m.scope == "project" for m in project_results)


def test_store_search_empty_query_returns_nothing(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory()])
    assert store.search("   ", scope="project") == []
    assert store.search("", scope="project") == []


# --- distiller ---------------------------------------------------------------


def test_distiller_parses_valid_batch(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {
                        "type": "fact",
                        "content": "login bug root cause is the token check",
                        "priority": 72,
                        "scope": "project",
                        "source": {"file": "src/auth.py", "line": 12},
                    },
                    {
                        "type": "todo",
                        "content": "write a regression test",
                        "priority": 50,
                        "scope": "session",
                    },
                ]
            )
        ]
    )

    result = L1Distiller(provider).distill(
        user_message="find the login bug",
        assistant_reply="the bug is in src/auth.py token check",
        run_id="run-1",
        session_id="session-1",
    )

    assert result.ok is True
    assert len(result.memories) == 2
    fact, todo = result.memories
    assert (fact.type, fact.scope, fact.priority) == ("fact", "project", 72)
    assert fact.source == {"file": "src/auth.py", "line": 12}
    assert (todo.type, todo.scope) == ("todo", "session")
    # The turn content is fed into the distill prompt.
    messages = provider.seen[0]
    assert messages[0]["role"] == "system"
    prompt = messages[1]["content"]
    assert "find the login bug" in prompt
    assert "the bug is in src/auth.py token check" in prompt


def test_distiller_degrades_on_bad_json(tmp_path) -> None:
    provider = ScriptedProvider(["not json at all"])
    result = L1Distiller(provider).distill(
        user_message="hi", assistant_reply="hey", run_id="r", session_id="s"
    )
    assert result.ok is False
    assert result.error_code == "bad_json"
    assert result.memories == []


def test_distiller_degrades_on_provider_error(tmp_path) -> None:
    class BrokenProvider:
        def complete(self, messages, *, options=None):
            raise RuntimeError("upstream down")

    result = L1Distiller(BrokenProvider()).distill(
        user_message="hi", assistant_reply="hey", run_id="r", session_id="s"
    )
    assert result.ok is False
    assert result.error_code == "provider"
    assert result.memories == []


def test_distiller_drops_invalid_items(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {"type": "fact", "content": "kept"},
                    {"type": "nonsense", "content": "bad type"},
                    {"content": "missing type"},
                    {"type": "fact", "content": "", "priority": 9999},
                ]
            )
        ]
    )
    result = L1Distiller(provider).distill(
        user_message="hi", assistant_reply="hey", run_id="r", session_id="s"
    )
    assert result.ok is True
    assert [m.content for m in result.memories] == ["kept"]


# --- recall ------------------------------------------------------------------


def test_recall_failure_returns_empty_not_exception(tmp_path) -> None:
    class BrokenStore(MemoryStore):
        def search(self, query, *, scope="project", limit=5):
            raise sqlite3.OperationalError("disk I/O error")

    store = BrokenStore(tmp_path / "memory" / "project.db")
    result = recall_memories(store, "auth deploy")

    assert result.ok is False
    assert result.memories == []
    assert result.error_code is not None


# --- context injection -------------------------------------------------------


def test_context_builder_injects_l1_tool_block(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory()])

    state = RunState.start("how do we deploy the auth service")
    builder = ContextBuilder(memory_store=store)
    messages = builder.build_messages(state, [])

    user_content = messages[-1]["content"]
    assert "<relevant-memories>" in user_content
    assert "make deploy-auth" in user_content
    assert state.metrics["l1_memories_injected"] == 1


def test_context_builder_without_store_is_backward_compatible(tmp_path) -> None:
    state = RunState.start("run a task")
    builder = ContextBuilder()
    messages = builder.build_messages(state, [])
    assert "<relevant-memories>" not in messages[-1]["content"]
    assert state.metrics.get("l1_memories_injected", 0) == 0


def test_context_builder_respects_total_chars_budget(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [
            _memory(content=f"fact number {index} " + ("x" * 200))
            for index in range(10)
        ]
    )
    state = RunState.start("fact number")
    builder = ContextBuilder(memory_store=store, memory_max_total_chars=300)
    messages = builder.build_messages(state, [])
    block = messages[-1]["content"]
    assert "more memories truncated" in block
    assert len(block) < 3_000


def test_format_relevant_memories_budget(tmp_path) -> None:
    long_memory = _memory(content="y" * 2_000)
    block = format_relevant_memories([long_memory], max_chars_per_memory=100)
    assert "..." in block
    assert len(block) < 300


# --- turn-end hook -----------------------------------------------------------


def test_turn_end_hook_distills_and_stores_memory(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    sessions = SessionStore(tmp_path / "sessions")
    record = sessions.create(tmp_path / "project")

    # Scripted provider: first call is the agent's final reply, second is the
    # distiller's memory batch (the hook fires after the loop returns).
    provider = ScriptedProvider(
        [
            '{"type":"final","answer":"use uv"}',
            json.dumps(
                [
                    {
                        "type": "decision",
                        "content": "use uv for dependency management",
                        "scope": "project",
                    }
                ]
            ),
        ]
    )
    hook = MemoryTurnHook(store, L1Distiller(provider))

    def loop_factory(state):
        return AgentLoop(provider, DisabledExecutor(), session=SessionManager())

    engine = SessionEngine(sessions, loop_factory=loop_factory, on_turn_end=hook)
    turn = engine.submit_turn(record.session_id, "how should we manage dependencies?")

    assert turn.status == "completed"
    assert store.count_memories() == 1
    assert [m.content for m in store.list_memories()] == [
        "use uv for dependency management"
    ]
    assert turn.state.metrics["memory_stored"] == 1
    assert turn.state.metrics["memory_distill_successes"] == 1


def test_turn_end_hook_distill_failure_does_not_block(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    sessions = SessionStore(tmp_path / "sessions")
    record = sessions.create(tmp_path / "project")

    # First reply is the agent's final action; second is what the distiller
    # receives — not a JSON array, so distillation fails gracefully.
    provider = ScriptedProvider(
        ['{"type":"final","answer":"done"}', "not a memory array"]
    )
    hook = MemoryTurnHook(store, L1Distiller(provider))

    def loop_factory(state):
        return AgentLoop(provider, DisabledExecutor(), session=SessionManager())

    engine = SessionEngine(sessions, loop_factory=loop_factory, on_turn_end=hook)
    turn = engine.submit_turn(record.session_id, "hi")

    assert turn.status == "completed"
    assert store.count_memories() == 0
    assert turn.state.metrics["memory_distill_failed"] == 1


def test_scope_project_memory_recalled_across_sessions(tmp_path) -> None:
    """§7 #2 / §5 cross-session continuity: a project-scoped memory distilled in
    one session is recalled by a *fresh* session in the same project without
    re-reading or re-distilling anything (the acceptance hook for scope=project).
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = MemoryStore(project_db_path(project_root, memory_root=tmp_path / "memory"))
    store.initialize()
    sessions = SessionStore(tmp_path / "sessions")

    session_a = sessions.create(project_root)
    provider = ScriptedProvider(
        [
            '{"type":"final","answer":"use uv"}',
            json.dumps(
                [
                    {
                        "type": "decision",
                        "content": "use uv for dependency management",
                        "scope": "project",
                        "priority": 80,
                    }
                ]
            ),
        ]
    )
    engine = SessionEngine(
        sessions,
        loop_factory=lambda state: AgentLoop(provider, DisabledExecutor(), session=SessionManager()),
        on_turn_end=MemoryTurnHook(store, L1Distiller(provider)),
    )
    turn_a = engine.submit_turn(session_a.session_id, "how should we manage dependencies?")
    assert turn_a.status == "completed"
    assert store.count_memories() == 1
    # The memory belongs to the project, not the session that wrote it.
    assert store.list_memories()[0].scope == "project"

    # A brand-new session in the same project starts cold (no session-scoped
    # memories), yet its first turn context recalls the project-scoped memory.
    session_b = sessions.create(project_root)
    assert session_b.session_id != session_a.session_id
    state_b = RunState.start(
        "what tool do we use for dependency management?", workspace_host_path=project_root
    )
    builder = ContextBuilder(memory_store=store)
    messages = builder.build_messages(state_b, [])

    assert "<relevant-memories>" in messages[-1]["content"]
    assert "use uv for dependency management" in messages[-1]["content"]
    assert state_b.metrics["l1_memories_recalled"] == 1