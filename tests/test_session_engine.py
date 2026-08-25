from dataclasses import dataclass, field

from minicc.core.loop import AgentLoop, DisabledExecutor
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine, SessionTurnResult
from minicc.core.session_store import SessionStore
from minicc.core.state import Observation
from minicc.policy.approval import ApprovalPolicy
from minicc.policy.base import PolicyChain


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


def _engine(store: SessionStore, provider: ScriptedProvider) -> SessionEngine:
    return SessionEngine(
        store,
        loop_factory=lambda state: AgentLoop(
            provider,
            DisabledExecutor(),
            session=SessionManager(),
        ),
    )


def test_session_engine_two_turns_persist_transcript_and_history(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"final","answer":"first reply"}',
            '{"type":"final","answer":"second reply"}',
        ]
    )
    engine = _engine(store, provider)

    turn1 = engine.submit_turn(record.session_id, "first msg")
    turn2 = engine.submit_turn(record.session_id, "second msg")

    assert turn1.status == "completed"
    assert turn1.assistant_reply == "first reply"
    assert turn2.status == "completed"
    assert turn2.assistant_reply == "second reply"
    assert turn1.run_id != turn2.run_id

    transcript = store.read_transcript(record.session_id)
    assert [m.role for m in transcript] == ["user", "assistant", "user", "assistant"]
    assert [m.content for m in transcript] == ["first msg", "first reply", "second msg", "second reply"]

    loaded = store.load(record.session_id)
    assert loaded.turns == [turn1.run_id, turn2.run_id]

    # First turn sees an empty history (no prior conversation).
    assert not any("first msg" == m.get("content") and m.get("role") == "user" for m in provider.seen[0])
    # Second turn replays the first turn's user + assistant rows into context.
    second_turn = provider.seen[1]
    assert {"role": "user", "content": "first msg"} in second_turn
    assert {"role": "assistant", "content": "first reply"} in second_turn
    # The current turn's goal is still present as its own user content.
    assert any("second msg" in m.get("content", "") for m in second_turn)


def test_session_engine_writes_run_evidence_without_history(tmp_path) -> None:
    import json

    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(replies=['{"type":"final","answer":"done"}'])
    engine = _engine(store, provider)

    turn = engine.submit_turn(record.session_id, "hello")

    state_path = store.session_runs_dir(record.session_id) / turn.run_id / "state.json"
    assert state_path.exists()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    # run evidence must not carry a second copy of conversation history (§5.1).
    assert "session_history" not in raw
    assert raw["goal"] == "hello"
    assert raw["status"] == "completed"
    assert raw["final_answer"] == "done"


def test_session_engine_waiting_approval_reply_is_question(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(replies=['{"type":"ask","question":"Pick a test command?"}'])
    engine = _engine(store, provider)

    turn: SessionTurnResult = engine.submit_turn(record.session_id, "run tests")

    assert turn.status == "waiting_approval"
    assert turn.assistant_reply == "Pick a test command?"
    transcript = store.read_transcript(record.session_id)
    assert transcript[-1].role == "assistant"
    assert transcript[-1].content == "Pick a test command?"


class RecordingExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, action, state):
        self.commands.append(action.command)
        return Observation(kind="command_result", exit_code=0, message="ok")


def _approval_engine(
    store: SessionStore,
    provider: ScriptedProvider,
    executor: RecordingExecutor,
    decision: str,
) -> SessionEngine:
    policy_chain = PolicyChain([ApprovalPolicy(enabled=True)])

    def loop_factory(state):
        return AgentLoop(
            provider,
            executor,
            session=SessionManager(),
            policy_chain=policy_chain,
        )

    return SessionEngine(
        store,
        loop_factory=loop_factory,
        executor=executor,
        on_approval=lambda state: decision,
    )


def test_session_engine_approval_continues_same_turn(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"bash","command":"rm -rf build","purpose":"clean artifacts"}',
            '{"type":"final","answer":"cleaned"}',
        ]
    )
    executor = RecordingExecutor()
    engine = _approval_engine(store, provider, executor, decision="approve")

    turn = engine.submit_turn(record.session_id, "clean up")

    assert turn.status == "completed"
    assert turn.assistant_reply == "cleaned"
    # The gated command was actually executed after approval.
    assert executor.commands == ["rm -rf build"]
    transcript = store.read_transcript(record.session_id)
    assert [m.role for m in transcript] == ["user", "assistant"]
    assert transcript[-1].content == "cleaned"


def test_session_engine_denial_fails_turn_without_executing(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=['{"type":"bash","command":"rm -rf build","purpose":"clean artifacts"}']
    )
    executor = RecordingExecutor()
    engine = _approval_engine(store, provider, executor, decision="deny: too risky")

    turn = engine.submit_turn(record.session_id, "clean up")

    assert turn.status == "failed"
    assert executor.commands == []  # denied commands never run
    assert "denied" in turn.assistant_reply.lower()


def _deferred_engine(
    store: SessionStore,
    provider: ScriptedProvider,
    executor: RecordingExecutor,
) -> SessionEngine:
    policy_chain = PolicyChain([ApprovalPolicy(enabled=True)])

    def loop_factory(state):
        return AgentLoop(
            provider,
            executor,
            session=SessionManager(),
            policy_chain=policy_chain,
        )

    return SessionEngine(store, loop_factory=loop_factory, executor=executor)


def test_session_engine_resolve_turn_after_deferred_approval(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"bash","command":"rm -rf build","purpose":"clean artifacts"}',
            '{"type":"final","answer":"cleaned"}',
        ]
    )
    executor = RecordingExecutor()
    engine = _deferred_engine(store, provider, executor)

    turn = engine.submit_turn(record.session_id, "clean up")
    assert turn.status == "waiting_approval"
    assert turn.state.pending_action is not None
    # The gated turn must not be written into the transcript until resolved.
    assert store.read_transcript(record.session_id) == []

    resolved = engine.resolve_turn(record.session_id, turn.run_id, "approve")
    assert resolved.status == "completed"
    assert resolved.assistant_reply == "cleaned"
    assert executor.commands == ["rm -rf build"]
    transcript = store.read_transcript(record.session_id)
    assert [m.role for m in transcript] == ["user", "assistant"]
    assert transcript[0].content == "clean up"
    assert transcript[1].content == "cleaned"
    assert store.load(record.session_id).turns == [turn.run_id]


def test_session_engine_resolve_turn_deny_fails(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=['{"type":"bash","command":"rm -rf build","purpose":"clean artifacts"}']
    )
    executor = RecordingExecutor()
    engine = _deferred_engine(store, provider, executor)

    turn = engine.submit_turn(record.session_id, "clean up")
    assert turn.status == "waiting_approval"

    resolved = engine.resolve_turn(record.session_id, turn.run_id, "deny: too risky")
    assert resolved.status == "failed"
    assert executor.commands == []
    transcript = store.read_transcript(record.session_id)
    assert transcript[-1].role == "assistant"
    assert "denied" in transcript[-1].content.lower()


# --- turn-end memory seam (V5 §6 #7 / V5.1 §4.1) -----------------------------


def test_turn_end_hook_fires_before_transcript_append(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"final","answer":"first reply"}',
            '{"type":"final","answer":"second reply"}',
        ]
    )
    transcript_lens_at_hook: list[int] = []
    seen_rows: list[str] = []

    def loop_factory(state):
        return AgentLoop(provider, DisabledExecutor(), session=SessionManager())

    def hook(session_id: str, result: SessionTurnResult) -> None:
        # The L1 seam fires *after* the loop returns but *before* the current
        # turn's rows land in the transcript (V5.1 §4.1) — prior turns only.
        transcript_lens_at_hook.append(len(store.read_transcript(session_id)))
        seen_rows.append(result.assistant_reply)

    engine = SessionEngine(store, loop_factory=loop_factory, on_turn_end=hook)
    engine.submit_turn(record.session_id, "one")
    engine.submit_turn(record.session_id, "two")

    assert transcript_lens_at_hook == [0, 2]
    assert seen_rows == ["first reply", "second reply"]


def test_turn_end_hook_error_degrades_to_metric(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(replies=['{"type":"final","answer":"ok"}'])

    def loop_factory(state):
        return AgentLoop(provider, DisabledExecutor(), session=SessionManager())

    def hook(session_id: str, result: SessionTurnResult) -> None:
        raise RuntimeError("distill boom")

    engine = SessionEngine(store, loop_factory=loop_factory, on_turn_end=hook)
    turn = engine.submit_turn(record.session_id, "hi")

    assert turn.status == "completed"
    assert turn.assistant_reply == "ok"
    assert turn.state.metrics["memory_turn_end_hook_errors"] == 1
    # The failing hook must never block the transcript commit.
    assert [m.role for m in store.read_transcript(record.session_id)] == ["user", "assistant"]


def test_turn_end_hook_skipped_until_deferred_turn_resolved(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"bash","command":"rm -rf build","purpose":"clean"}',
            '{"type":"final","answer":"cleaned"}',
        ]
    )
    executor = RecordingExecutor()
    policy_chain = PolicyChain([ApprovalPolicy(enabled=True)])
    calls: list[str] = []

    def loop_factory(state):
        return AgentLoop(provider, executor, session=SessionManager(), policy_chain=policy_chain)

    def hook(session_id: str, result: SessionTurnResult) -> None:
        calls.append(result.user_message)

    engine = SessionEngine(
        store,
        loop_factory=loop_factory,
        executor=executor,
        on_turn_end=hook,
    )

    turn = engine.submit_turn(record.session_id, "clean")
    assert turn.status == "waiting_approval"
    assert calls == []  # a paused turn is not yet a committed turn

    resolved = engine.resolve_turn(record.session_id, turn.run_id, "approve")
    assert resolved.status == "completed"
    assert calls == ["clean"]  # hook fires exactly once, on commit