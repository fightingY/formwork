import threading
from pathlib import Path

import pytest

from minicc.core.compaction import CompactionError, CompactionManager
from minicc.core.events import EventLog, EventValidationError
from minicc.core.projections import ProjectionRegistry, default_projections
from minicc.core.protocol import BashAction
from minicc.core.recovery import recover_session
from minicc.core.replay import replay_round_trip
from minicc.core.session import SessionManager
from minicc.core.session_store import SessionStore
from minicc.core.spill import SpillStore
from minicc.core.state import RunState
from minicc.core.stream import StreamAssembler
from minicc.policy.base import PolicyDecision
from minicc.sandbox.local_runner import LocalCommandExecutor


def registry(log: EventLog) -> ProjectionRegistry:
    r = ProjectionRegistry()
    for projection in default_projections():
        r.register(projection)
    r.fold(log.session_id or "s", log.events)
    return r


def test_event_log_pairs_tools_and_repairs_crash(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    log.append("turn/start", {"turn": 1})
    log.append("step/start", {"turn": 1, "step": 1})
    log.append("tool/call", {"turn": 1, "step": 1, "callId": "c1", "name": "read", "started": True})
    with pytest.raises(EventValidationError):
        log.append("tool/result", {"callId": "missing"})
    report = recover_session(log)
    assert report["repaired_events"] == [4, 5]
    assert report["unknown_tool_outcomes"][0]["callId"] == "c1"
    assert report["allow_automatic_retry"] is False


def test_event_log_rejects_duplicate_tool_result(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    log.append("tool/call", {"call_id": "c1", "turn": 0, "step": 0, "name": "read"})
    log.append("tool/result", {"call_id": "c1", "turn": 0, "step": 0, "content": "ok"})
    with pytest.raises(EventValidationError, match="only be recorded once"):
        log.append("tool/result", {"call_id": "c1", "turn": 0, "step": 0, "content": "again"})


def test_live_cold_and_cache_suffix_projection_match(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path)
    sid = record.session_id
    store.append_event(sid, "turn/start", {"turn": 0})
    store.append_message(sid, "user", "hello")
    store.append_message(sid, "assistant", "world")
    events = store.events(sid)
    live = store.projection_registry(sid)
    cold = store.projection_registry(sid)
    assert live.snapshot(sid).values == cold.snapshot(sid).values
    cache = live.cache_rows(sid)
    restored = ProjectionRegistry()
    for projection in default_projections():
        restored.register(projection)
    restored.restore_cache(sid, cache, events)
    assert restored.snapshot(sid).values == live.snapshot(sid).values


def test_stream_chunks_are_durable_before_assistant_message(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    assembler = StreamAssembler(0, 0)
    assembler.accept(log, {"type": "block-start", "index": 0, "blockType": "reasoning"})
    assembler.accept(log, {"type": "reasoning-delta", "index": 0, "text": "think"})
    assembler.accept(log, {"type": "text-delta", "text": "done"})
    assembler.commit(log)
    assert [event.type for event in log.events] == [
        "assistant/chunk",
        "assistant/chunk",
        "assistant/chunk",
        "assistant/message",
    ]
    assert log.events[-1].data["message"]["content"][0]["type"] == "reasoning"


def test_compaction_replaces_surface_without_deleting_history(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    log.append("turn/start", {"turn": 0})
    log.append("user/message", {"role": "user", "content": "a" * 2000})
    log.append("assistant/message", {"message": {"role": "assistant", "content": "b" * 2000}})
    manager = CompactionManager(log, context_window=10, threshold_ratio=0.1)
    result = manager.compact(force=True)
    assert result is not None
    assert any(event.type == "compaction/start" for event in log.events)
    assert any(event.type == "compaction/summary" for event in log.events)
    assert any(event.type == "compaction/end" for event in log.events)
    surface = registry(log).value("s", "surface")
    assert surface["replacement_generation"] == 1
    assert len(log.events) > len(surface["event_seqs"])


def test_compaction_does_not_write_nonshrinking_summary(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    log.append("turn/start", {"turn": 0})
    log.append("user/message", {"role": "user", "content": "short"})
    log.append("assistant/message", {"message": {"role": "assistant", "content": "reply"}})
    manager = CompactionManager(log, context_window=1, threshold_ratio=0)
    with pytest.raises(CompactionError):
        manager.compact(force=True, summary_model=lambda text: text)
    assert not any(event.type == "compaction/summary" for event in log.events)


def test_spill_writes_full_output_and_returns_bounded_model_view(tmp_path: Path) -> None:
    result = SpillStore(tmp_path / "spill", preview_chars=10).write("0123456789abcdef", "out.txt")
    assert result.bytes == 16
    assert result.preview == "0123456789"
    assert Path(result.locator).read_text() == "0123456789abcdef"


def test_session_reload_rebuilds_same_projection_from_disk(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path)
    sid = record.session_id
    store.append_event(sid, "turn/start", {"turn": 0})
    store.append_message(sid, "user", "reload me")
    store.append_message(sid, "assistant", "persisted")
    before = store.projection_registry(sid).snapshot(sid).values
    reloaded = SessionStore(tmp_path / "sessions").projection_registry(sid).snapshot(sid).values
    assert before == reloaded


def test_approval_request_and_result_are_durable_events(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    record = store.create(tmp_path)
    state = RunState.start("approve", workspace_host_path=tmp_path)
    state._event_log = store.event_log(record.session_id)
    manager = SessionManager()
    manager.request_approval(
        state,
        BashAction(command="echo ok"),
        PolicyDecision(type="require_approval", reason="needs approval", policy_name="test"),
    )
    manager.approve(state)
    events = store.events(record.session_id)
    assert [e.type for e in events if e.type.startswith("approval/")] == [
        "approval/request",
        "approval/result",
    ]
    assert events[-1].data["tool_call_id"] == events[-2].data["tool_call_id"]


def test_local_executor_observes_runtime_cancel_token(tmp_path: Path) -> None:
    state = RunState.start("cancel", workspace_host_path=tmp_path)
    token = threading.Event()
    state._cancel_token = token
    token.set()
    observation = LocalCommandExecutor().run(
        BashAction(command="python -c \"import time; time.sleep(5)\"", timeout_sec=10), state
    )
    assert observation.kind == "command_error"
    assert "cancelled" in observation.message.lower()


def test_event_log_replay_round_trip_is_deterministic(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", session_id="s")
    log.append("session/start", {"session_id": "s", "format_version": 2})
    log.append("user/message", {"role": "user", "content": "replay"})
    assert replay_round_trip(log.path)
