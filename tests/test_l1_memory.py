import json
import sqlite3
from dataclasses import dataclass, field
from types import SimpleNamespace

from minicc.core.context import ContextBuilder, ContextConfig
from minicc.core.events import EventLog
from minicc.core.loop import AgentLoop, DisabledExecutor, LoopConfig
from minicc.core.protocol import TOOLS, BashAction
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage, NativeToolCall
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine
from minicc.core.session_store import SessionStore
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.memory.l1 import (
    L1Distiller,
    L1Memory,
    MemoryStore,
    MemoryTurnHook,
    PersonaEntry,
    ScenarioEntry,
    format_relevant_memories,
    project_db_path,
    recall_memories,
    recall_scoped_memories,
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


def test_scoped_recall_keeps_session_memories_isolated(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [
            _memory(content="session one deployment note", scope="session", session_id="s1"),
            _memory(content="session two deployment note", scope="session", session_id="s2"),
            _memory(content="project deployment command", scope="project"),
        ]
    )

    result = recall_scoped_memories(store, "deployment note command", session_id="s1", limit=5)

    assert result.ok is True
    contents = [memory.content for memory in result.memories]
    assert "session one deployment note" in contents
    assert "session two deployment note" not in contents
    assert "project deployment command" in contents


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
    # The harness stamps the authoritative run id into the provenance source.
    assert fact.source == {"file": "src/auth.py", "line": 12, "run_id": "run-1"}
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
        return AgentLoop(
            provider,
            DisabledExecutor(),
            session=SessionManager(),
            config=LoopConfig(model_options=CompletionOptions(tools=TOOLS, tool_choice="required")),
        )

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
        return AgentLoop(
            provider,
            DisabledExecutor(),
            session=SessionManager(),
            config=LoopConfig(model_options=CompletionOptions(tools=TOOLS, tool_choice="required")),
        )

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
        loop_factory=lambda state: AgentLoop(
            provider,
            DisabledExecutor(),
            session=SessionManager(),
            config=LoopConfig(model_options=CompletionOptions(tools=TOOLS, tool_choice="required")),
        ),
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


# --- spec-named L1 contracts -------------------------------------------------


def test_l1_topic_key_is_persisted(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    anchored = L1Memory(
        type="fact",
        content="the token check is buggy",
        priority=50,
        scope="project",
        source={"file": "SRC\\Auth.py", "run_id": "run-1"},
    )
    floating = L1Memory(
        type="fact", content="no anchor here", priority=50, scope="project"
    )

    store.add_memories([anchored, floating])
    persisted = {m.content: m.topic_key for m in store.list_memories()}
    # Normalized (lowercase, separators unified) and queryable at L2 clustering.
    assert persisted["the token check is buggy"] == "src/auth.py"
    assert persisted["no anchor here"] == ""


def test_l1_scope_validation(tmp_path) -> None:
    """Oversized output is dropped, not truncated into a half-fact; session
    scope without a session id falls back to the distiller-provided one."""
    provider = ScriptedProvider(
        [
            json.dumps(
                [
                    {"type": "fact", "content": "x" * 3000, "scope": "project"},
                    {"type": "todo", "content": "session todo", "scope": "session"},
                ]
            )
        ]
    )
    result = L1Distiller(provider).distill(
        user_message="u",
        assistant_reply="a",
        run_id="run-1",
        session_id="sess-42",
    )
    assert result.ok is True
    assert [memory.content for memory in result.memories] == ["session todo"]
    assert result.memories[0].session_id == "sess-42"


def test_l1_source_range_is_preserved(tmp_path) -> None:
    """Every stored memory keeps an auditable L0 anchor: run id + event range."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    provider = ScriptedProvider(
        [json.dumps([{"type": "fact", "content": "anchored fact", "scope": "project"}])]
    )
    outcome = L1Distiller(provider).distill(
        user_message="u",
        assistant_reply="a",
        run_id="run-7",
        session_id="s",
        event_range=(11, 17),
    )
    store.add_memories(outcome.memories)

    memory = store.list_memories()[0]
    assert memory.source["run_id"] == "run-7"
    assert memory.source["event_seq_start"] == 11
    assert memory.source["event_seq_end"] == 17
    assert memory.source_run_id == "run-7"


def test_l1_merge_preserves_provenance(tmp_path) -> None:
    """Merging keeps the original provenance and only ratchets confidence up."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [
            L1Memory(
                type="fact",
                content="auth bug: token check",
                priority=60,
                scope="project",
                confidence=0.9,
                source={"file": "src/auth.py", "run_id": "run-1"},
            )
        ]
    )
    incoming = L1Memory(
        type="fact",
        content="auth bug: token check + session cookie",
        priority=50,
        scope="project",
        confidence=0.6,
        source={"file": "src/auth.py", "run_id": "run-2"},
    )

    assert store.update_memory(1, incoming.content, memory=incoming, merge=True)

    merged = store.get_memory(1)
    assert merged is not None
    assert merged.source["run_id"] == ["run-1", "run-2"]  # conflicting values list
    assert merged.confidence == 0.9  # ratchets up, never down
    assert merged.source_run_id == "run-1"  # the original row keeps its anchor


def test_recall_event_contains_diagnostics(tmp_path) -> None:
    """The memory/recall audit event carries the full retrieval diagnostics."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories([_memory(content="deploy the auth service with make deploy-auth")])
    log = EventLog(tmp_path / "events.jsonl")

    state = RunState.start("how do we deploy the auth service")
    state._event_log = log
    builder = ContextBuilder(memory_store=store)
    builder.build_messages(state, [])

    recalls = [event for event in log.events if event.type == "memory/recall"]
    assert len(recalls) == 1
    data = recalls[0].data
    assert data["ok"] is True
    assert data["scope_order"] == ["project"]  # no session id → project only
    diagnostics = data["retrieval_mode"]
    assert diagnostics == {"project": "bm25"}  # no embedder → BM25 never fails
    assert data["bm25_candidates"]["project"] == [1]
    assert data["vector_candidates"]["project"] == []
    assert data["rrf_rank"]["project"] == {"1": 1}
    assert data["final_record_ids"]["project"] == [1]


# --- snapshot lifecycle (spec §8) --------------------------------------------


def _project_one_turn(store: MemoryStore, tmp_path, *, reply: str) -> SimpleNamespace:
    """Run one synchronous projection pass and return the shim result."""
    provider = ScriptedProvider(
        [json.dumps([{"type": "fact", "content": reply, "scope": "project"}])]
    )
    hook = MemoryTurnHook(store, L1Distiller(provider))
    state = RunState.start("project a turn")
    state._event_log = EventLog(tmp_path / "events.jsonl")
    result = SimpleNamespace(state=state, user_message="u", assistant_reply="a", run_id="r1")
    hook("sess-1", result)
    return result


def test_snapshot_published_after_projection(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="token bug", topic_key="src/auth.py")
    )
    store.upsert_persona(
        PersonaEntry(style="tests first", origin="auto", state="confirmed")
    )

    result = _project_one_turn(store, tmp_path, reply="a fresh fact")

    snapshot_id = store.active_snapshot_id(project_id="project")
    assert snapshot_id is not None
    assert result.state.metrics["memory_snapshot_published"] == snapshot_id
    # Only L2/L3 freeze into the prompt-stable view; L1 stays dynamically recalled.
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


def test_current_run_keeps_snapshot_stable(tmp_path) -> None:
    """A running prompt keeps its pinned snapshot; a fresh run sees the latest."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="old summary", topic_key="k1")
    )
    store.publish_snapshot(project_id="project")

    state = RunState.start("goal")
    builder = ContextBuilder(memory_store=store)
    assert "old summary" in builder.build_messages(state, [])[0]["content"]

    # A background projection publishes snapshot 2 with a new scenario mid-run.
    store.upsert_scenario(
        ScenarioEntry(scenario="billing", summary="new summary", topic_key="k2")
    )
    store.publish_snapshot(project_id="project")

    assert "old summary" in builder.build_messages(state, [])[0]["content"]
    assert "new summary" not in builder.build_messages(state, [])[0]["content"]

    fresh = ContextBuilder(memory_store=store).build_messages(RunState.start("goal"), [])
    assert "new summary" in fresh[0]["content"]


def _step(command: str, message: str) -> TrajectoryStep:
    return TrajectoryStep(
        action=BashAction(command=command, purpose=message),
        observation=Observation(kind="command_result", exit_code=0, message=message),
    )


def test_compaction_rolls_memory_snapshot_epoch(tmp_path) -> None:
    """Compaction starts a new prompt epoch, which re-resolves the snapshot —
    so a newly published background projection is picked up exactly there."""
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="old summary", topic_key="k1")
    )
    store.publish_snapshot(project_id="project")

    builder = ContextBuilder(
        ContextConfig(
            prompt_layout="epoch",
            recent_turns=1,
            max_prompt_chars=10,
            summary_max_chars=2_000,
        ),
        memory_store=store,
    )
    state = RunState.start("Inspect repository")
    trajectory = [_step("pwd", "first"), _step("ls", "second")]

    builder.build_messages(state, trajectory)
    assert state.metrics["memory_snapshot_id"] == 1

    # Publish snapshot 2 mid-run, then compact: the epoch rolls and the pin
    # clears, so the next prompt re-resolves to the latest snapshot.
    store.upsert_scenario(
        ScenarioEntry(scenario="billing", summary="new summary", topic_key="k2")
    )
    store.publish_snapshot(project_id="project")
    builder.maybe_compact(state, trajectory)
    builder.build_messages(state, trajectory)

    assert state._memory_snapshot_id == 2
    assert state.metrics["cache_prefix_reset_reason"] == "compaction_epoch_rollover"


def test_new_session_uses_latest_snapshot(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.upsert_scenario(
        ScenarioEntry(scenario="auth", summary="old summary", topic_key="k1")
    )
    store.publish_snapshot(project_id="project")
    old_state = RunState.start("goal")
    old_builder = ContextBuilder(memory_store=store)
    assert "old summary" in old_builder.build_messages(old_state, [])[0]["content"]

    # Later, session B's projection adds a scenario; a brand-new session starts
    # on the latest snapshot without touching session A's pinned view.
    result = _project_one_turn(store, tmp_path, reply="ignored")
    store.upsert_scenario(
        ScenarioEntry(scenario="billing", summary="new summary", topic_key="k2")
    )
    store.publish_snapshot(project_id="project")

    new_builder = ContextBuilder(memory_store=store)
    new_content = new_builder.build_messages(RunState.start("goal"), [])[0]["content"]
    assert "new summary" in new_content
    assert "old summary" in old_builder.build_messages(old_state, [])[0]["content"]
    assert result.state.metrics["memory_snapshot_published"] >= 2
