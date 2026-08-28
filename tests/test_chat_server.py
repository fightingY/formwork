import json
import queue
from dataclasses import dataclass, field

import pytest

from minicc.core.loop import AgentLoop, LoopConfig
from minicc.core.protocol import TOOLS
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage, NativeToolCall
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine
from minicc.core.session_store import SessionStore
from minicc.core.state import Observation
from minicc.core.tooling import HybridToolRunner, ToolCallScheduler
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


class RecordingExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, action, state):
        self.commands.append(action.command)
        return Observation(kind="command_result", exit_code=0, message="ok")


def _deferred_engine_factory(store, provider, executor):
    policy_chain = PolicyChain([ApprovalPolicy(enabled=True)])

    def loop_factory(state):
        scheduler = ToolCallScheduler(HybridToolRunner(executor))
        return AgentLoop(
            provider,
            executor,
            session=SessionManager(),
            policy_chain=policy_chain,
            tool_scheduler=scheduler,
            config=LoopConfig(model_options=CompletionOptions(tools=TOOLS, tool_choice="required")),
        )

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
    assert 'id="viewOps"' in html
    assert 'id="opsView"' in html
    assert 'src="/ops"' in html
    assert "task-card" in html
    assert "addActivity" in html
    assert "Trace #" not in html

def test_progress_event_projects_trace_to_user_activity() -> None:
    from minicc.server.chat import progress_event

    assert progress_event(
        {"event": "sandbox_exec_started", "sequence": 4, "run_id": "run-1"}
    ) is None
    assert progress_event({"event": "unknown"}) is None


def test_progress_event_prefers_model_progress_over_lifecycle_labels() -> None:
    from minicc.server.chat import progress_event

    event = progress_event(
        {
            "event": "action_parsed",
            "run_id": "run-2",
            "action": {
                "type": "tool_calls",
                "progress": "我先读取入口和配置，确认请求如何进入执行链。",
            },
        }
    )
    assert event == {
        "type": "activity",
        "label": "我先读取入口和配置，确认请求如何进入执行链。",
        "event": "agent_progress",
        "run_id": "run-2",
    }
    assert progress_event({"event": "model_response", "run_id": "run-2"}) is None


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


def test_transcript_payload_recovers_public_progress_from_uncommitted_run(tmp_path) -> None:
    from minicc.server.chat import transcript_payload

    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path / "project")
    run_dir = store.session_runs_dir(record.session_id) / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "trace.jsonl").write_text(
        '{"event":"run_started","run_id":"run-1","goal":"检查秒杀入口","status":"running"}\n'
        '{"event":"action_parsed","run_id":"run-1","action":{"type":"tool_calls",'
        '"progress":"我先读取入口和 Lua 脚本，确认库存校验链路。"}}\n',
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        '{"run_id":"run-1","goal":"检查秒杀入口","status":"running"}',
        encoding="utf-8",
    )

    payload = transcript_payload(store, record.session_id)
    assert payload == [
        {
            "seq": 1,
            "run_id": "run-1",
            "role": "user",
            "content": "检查秒杀入口",
            "activities": ["我先读取入口和 Lua 脚本，确认库存校验链路。"],
            "run_status": "running",
            "in_progress": True,
        }
    ]


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
        replies=[
            '{"type":"bash","command":"rm -rf build","purpose":"clean"}',
            '{"type":"final","answer":"skipped the cleanup since it was denied"}',
        ]
    )
    executor = RecordingExecutor()
    factory = _deferred_engine_factory(store, provider, executor)

    waiting = execute_turn(factory, record.session_id, "clean")
    resolved = resolve_approval(factory, record.session_id, waiting["run_id"], "deny: too risky")

    assert resolved["type"] == "turn_done"
    assert resolved["status"] == "completed"
    assert executor.commands == []
    transcript = store.read_transcript(record.session_id)
    assert transcript[-1].role == "assistant"
    assert "denied" in transcript[-1].content.lower()
