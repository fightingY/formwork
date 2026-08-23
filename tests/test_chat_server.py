import queue
from dataclasses import dataclass, field

import pytest

from minicc.core.loop import AgentLoop
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine
from minicc.core.session_store import SessionStore
from minicc.core.state import Observation
from minicc.policy.approval import ApprovalPolicy
from minicc.policy.base import PolicyChain
from minicc.server.chat import (
    ChatBroker,
    _is_safe_run_id,
    _is_safe_session_id,
    execute_turn,
    render_chat_index,
    resolve_approval,
    sessions_payload,
    transcript_payload,
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
        return ModelResponse(
            text=self.replies.pop(0),
            raw={},
            usage=ModelUsage(),
            latency_ms=1,
        )


class RecordingExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, action, state):
        self.commands.append(action.command)
        return Observation(kind="command_result", exit_code=0, message="ok")


def _deferred_engine_factory(store, provider, executor):
    policy_chain = PolicyChain([ApprovalPolicy(enabled=True)])

    def loop_factory(state):
        return AgentLoop(provider, executor, session=SessionManager(), policy_chain=policy_chain)

    def factory() -> SessionEngine:
        return SessionEngine(store, loop_factory=loop_factory, executor=executor)

    return factory


def test_chat_index_renders_single_page_with_sse() -> None:
    html = render_chat_index()
    assert "miniCC Chat" in html
    assert "/api/sessions" in html
    assert "EventSource" in html
    assert "steerInput" in html
    assert "approveBtn" in html


def test_sessions_and_transcript_payloads(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project", title="titled")
    store.append_message(record.session_id, "user", "hi", run_id="r1")

    sessions = sessions_payload(store)
    assert sessions[0]["session_id"] == record.session_id
    assert sessions[0]["title"] == "titled"
    assert sessions[0]["turn_count"] == 0

    transcript = transcript_payload(store, record.session_id)
    assert transcript[0]["role"] == "user"
    assert transcript[0]["content"] == "hi"
    assert transcript[0]["seq"] == 1


def test_safety_guards_reject_path_traversal() -> None:
    assert _is_safe_session_id("abc-123") is True
    assert _is_safe_session_id("../outside") is False
    assert _is_safe_session_id("a/b") is False
    assert _is_safe_session_id("") is False
    assert _is_safe_run_id("r1") is True
    assert _is_safe_run_id(".") is False


def test_broker_fans_out_and_unsubscribes() -> None:
    broker = ChatBroker()
    sub = broker.subscribe("sid")
    broker.publish("sid", {"type": "turn_done"})
    assert sub.get(timeout=1) == {"type": "turn_done"}

    broker.unsubscribe("sid", sub)
    broker.publish("sid", {"type": "ignored"})
    with pytest.raises(queue.Empty):
        sub.get(timeout=0.05)


def test_execute_turn_completes_and_appends_transcript(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(replies=['{"type":"final","answer":"done"}'])
    executor = RecordingExecutor()
    factory = _deferred_engine_factory(store, provider, executor)

    event = execute_turn(factory, record.session_id, "hello")

    assert event["type"] == "turn_done"
    assert event["status"] == "completed"
    assert event["assistant_reply"] == "done"
    assert event["run_id"]
    assert [m.role for m in store.read_transcript(record.session_id)] == ["user", "assistant"]


def test_execute_turn_pauses_on_approval_without_transcript(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"bash","command":"rm -rf build","purpose":"clean"}',
            '{"type":"final","answer":"cleaned"}',
        ]
    )
    executor = RecordingExecutor()
    factory = _deferred_engine_factory(store, provider, executor)

    event = execute_turn(factory, record.session_id, "clean")

    assert event["type"] == "turn_waiting_approval"
    assert event["status"] == "waiting_approval"
    assert event["pending_command"] == "rm -rf build"
    assert store.read_transcript(record.session_id) == []
    assert executor.commands == []


def test_resolve_approval_approve_completes_and_executes(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=[
            '{"type":"bash","command":"rm -rf build","purpose":"clean"}',
            '{"type":"final","answer":"cleaned"}',
        ]
    )
    executor = RecordingExecutor()
    factory = _deferred_engine_factory(store, provider, executor)

    waiting = execute_turn(factory, record.session_id, "clean")
    resolved = resolve_approval(factory, record.session_id, waiting["run_id"], "approve")

    assert resolved["type"] == "turn_done"
    assert resolved["status"] == "completed"
    assert executor.commands == ["rm -rf build"]
    assert [m.role for m in store.read_transcript(record.session_id)] == ["user", "assistant"]


def test_resolve_approval_deny_fails_without_executing(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    provider = ScriptedProvider(
        replies=['{"type":"bash","command":"rm -rf build","purpose":"clean"}']
    )
    executor = RecordingExecutor()
    factory = _deferred_engine_factory(store, provider, executor)

    waiting = execute_turn(factory, record.session_id, "clean")
    resolved = resolve_approval(factory, record.session_id, waiting["run_id"], "deny: too risky")

    assert resolved["type"] == "turn_done"
    assert resolved["status"] == "failed"
    assert executor.commands == []
    transcript = store.read_transcript(record.session_id)
    assert transcript[-1].role == "assistant"
    assert "denied" in transcript[-1].content.lower()