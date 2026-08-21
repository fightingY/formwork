import json
import threading
import time
from pathlib import Path

import pytest

from minicc.core.context import ContextBuilder
from minicc.core.loop import AgentLoop, LoopConfig
from minicc.core.protocol import ProtocolError, ToolCall, ToolCallsAction, parse_action
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState
from minicc.core.tooling import (
    FileSystemCapability,
    HybridToolRunner,
    ToolCallScheduler,
    ToolResult,
)
from minicc.trace.recorder import TraceRecorder


class NoopBashExecutor:
    def run(self, action, state: RunState) -> Observation:
        return Observation(kind="command_result", exit_code=0, stdout_preview=action.command)


def test_tool_calls_protocol_preserves_order_and_normalizes_bash() -> None:
    action = parse_action(
        json.dumps(
            {
                "type": "tool_calls",
                "calls": [
                    {"id": "r1", "tool": "read", "arguments": {"path": "a.py"}},
                    {"id": "b1", "tool": "bash", "arguments": {"command": " pytest -q "}},
                ],
            }
        ),
        max_timeout_sec=20,
    )

    assert isinstance(action, ToolCallsAction)
    assert [call.id for call in action.calls] == ["r1", "b1"]
    assert action.calls[1].arguments["command"] == "pytest -q"
    assert action.calls[1].arguments["timeout_sec"] == 20


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"type": "tool_calls", "calls": []}, "non-empty array"),
        (
            {
                "type": "tool_calls",
                "calls": [
                    {"id": "same", "tool": "read", "arguments": {"path": "a"}},
                    {"id": "same", "tool": "read", "arguments": {"path": "b"}},
                ],
            },
            "Duplicate tool call id",
        ),
        (
            {"type": "tool_calls", "calls": [{"id": "x", "tool": "grep", "arguments": {}}]},
            "Unknown tool",
        ),
    ],
)
def test_tool_calls_protocol_rejects_invalid_envelopes(payload: dict, message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        parse_action(json.dumps(payload))


def test_tool_calls_protocol_honors_configurable_limit() -> None:
    payload = {
        "type": "tool_calls",
        "calls": [
            {"id": f"r{index}", "tool": "read", "arguments": {"path": "a"}}
            for index in range(3)
        ],
    }
    with pytest.raises(ProtocolError, match="at most 2"):
        parse_action(json.dumps(payload), max_tool_calls=2)


def test_read_is_bounded_numbered_and_returns_version(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    state = RunState.start("read", workspace_host_path=tmp_path)

    result = FileSystemCapability(max_read_lines=2).run(
        ToolCall("r1", "read", {"path": "a.txt", "offset": 2, "limit": 10}), state
    )

    assert result.is_error is False
    assert result.content["content"] == "2: two\n3: three"
    assert result.content["total_lines"] == 3
    assert result.content["sha256"].startswith("sha256:")


def test_fs_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    state = RunState.start("boundary", workspace_host_path=workspace)
    fs = FileSystemCapability()

    parent = fs.run(ToolCall("r1", "read", {"path": "../outside.txt"}), state)
    assert parent.content["error_code"] == "PATH_OUT_OF_BOUNDS"

    try:
        (workspace / "link.txt").symlink_to(outside)
    except OSError:
        fallback = fs.run(ToolCall("r2", "read", {"path": "outside.txt"}), state)
        assert fallback.content["error_code"] == "READ_NOT_FOUND"
        return
    symlink = fs.run(ToolCall("r2", "read", {"path": "link.txt"}), state)
    assert symlink.content["error_code"] == "PATH_OUT_OF_BOUNDS"


def test_edit_requires_hash_and_rejects_version_conflict(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    state = RunState.start("edit", workspace_host_path=tmp_path)
    fs = FileSystemCapability()

    missing = fs.run(
        ToolCall("e1", "edit", {"path": "a.txt", "old_string": "old", "new_string": "new"}),
        state,
    )
    conflict = fs.run(
        ToolCall(
            "e2",
            "edit",
            {
                "path": "a.txt",
                "old_string": "old",
                "new_string": "new",
                "expected_hash": "sha256:stale",
            },
        ),
        state,
    )

    assert missing.content["error_code"] == "EDIT_EXPECTED_HASH_REQUIRED"
    assert conflict.content["error_code"] == "EDIT_VERSION_CONFLICT"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_edit_exact_and_write_existing_version_contract(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    state = RunState.start("modify", workspace_host_path=tmp_path)
    fs = FileSystemCapability()
    read = fs.run(ToolCall("r", "read", {"path": "a.txt"}), state)

    edited = fs.run(
        ToolCall(
            "e",
            "edit",
            {
                "path": "a.txt",
                "old_string": "old",
                "new_string": "new",
                "expected_hash": read.content["sha256"],
            },
        ),
        state,
    )
    missing = fs.run(ToolCall("w", "write", {"path": "a.txt", "content": "full\n"}), state)
    created = fs.run(ToolCall("w2", "write", {"path": "new.txt", "content": "new\n"}), state)

    assert edited.is_error is False
    assert "-old" in edited.content["diff"] and "+new" in edited.content["diff"]
    assert missing.content["error_code"] == "WRITE_EXPECTED_HASH_REQUIRED"
    assert created.content["created"] is True


def test_scheduler_overlaps_reads_drains_before_exclusive_and_orders_results() -> None:
    events: list[tuple[str, str]] = []
    lock = threading.Lock()

    class RecordingRunner:
        def execution_mode(self, call: ToolCall) -> str:
            return "parallel" if call.tool == "read" else "exclusive"

        def run(self, call: ToolCall, state: RunState) -> ToolResult:
            with lock:
                events.append(("start", call.id))
            if call.tool == "read":
                time.sleep(0.03 if call.id == "slow" else 0.01)
            with lock:
                events.append(("end", call.id))
            return ToolResult(
                call.id,
                call.tool,
                0,
                "parallel" if call.tool == "read" else "exclusive",
                {"id": call.id},
            )

    action = ToolCallsAction(
        (
            ToolCall("slow", "read", {"path": "a"}),
            ToolCall("fast", "read", {"path": "b"}),
            ToolCall("write", "write", {"path": "c", "content": "x"}),
            ToolCall("after", "read", {"path": "d"}),
        )
    )
    results = ToolCallScheduler(RecordingRunner(), max_parallel_tool_calls=2).dispatch(
        action, RunState.start("schedule")
    )

    assert [result.call_id for result in results] == ["slow", "fast", "write", "after"]
    assert events.index(("start", "fast")) < events.index(("end", "slow"))
    assert events.index(("start", "write")) > events.index(("end", "slow"))
    assert events.index(("start", "write")) > events.index(("end", "fast"))
    assert events.index(("start", "after")) > events.index(("end", "write"))


def test_hybrid_bash_adapter_reuses_existing_executor() -> None:
    result = HybridToolRunner(NoopBashExecutor()).run(
        ToolCall("b", "bash", {"command": "pytest -q", "timeout_sec": 5}),
        RunState.start("bash"),
    )

    assert result.content["stdout"] == "pytest -q"


def test_hybrid_loop_commits_ordered_tool_results_and_next_control_turn(tmp_path: Path) -> None:
    class Provider:
        def __init__(self) -> None:
            self.responses = [
                json.dumps(
                    {
                        "type": "tool_calls",
                        "calls": [
                            {"id": "a", "tool": "read", "arguments": {"path": "a.txt"}},
                            {"id": "b", "tool": "read", "arguments": {"path": "b.txt"}},
                        ],
                    }
                ),
                '{"type":"final","answer":"done"}',
            ]

        def complete(self, messages, *, options: CompletionOptions | None = None) -> ModelResponse:
            return ModelResponse(
                text=self.responses.pop(0),
                raw={},
                usage=ModelUsage(prompt_tokens=1, completion_tokens=1),
                latency_ms=1,
            )

    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    state = RunState.start("hybrid", workspace_host_path=tmp_path, run_dir=tmp_path / "run")
    state.metrics["profile"] = "hybrid-v3.6"
    trace = TraceRecorder()
    runner = HybridToolRunner(NoopBashExecutor())
    loop = AgentLoop(
        Provider(),
        NoopBashExecutor(),
        session=SessionManager(runs_root=tmp_path / "runs"),
        trace=trace,
        config=LoopConfig(profile="hybrid-v3.6"),
        tool_scheduler=ToolCallScheduler(runner, max_parallel_tool_calls=2),
    )

    result = loop.run(state)

    assert result.state.status == "completed"
    assert result.state.metrics["tool_calls"] == 2
    assert len(result.trajectory) == 1
    assert result.trajectory[0].observation.stdout_preview.count('"call_id"') == 2
    assert [event["event"] for event in trace.events if event["event"].startswith("tool/")] == [
        "tool/call",
        "tool/call",
        "tool/result",
        "tool/result",
    ]
    call_events = [event for event in trace.events if event["event"] == "tool/call"]
    assert [event["call_id"] for event in call_events] == ["a", "b"]
    assert "hybrid-v3.6" in ContextBuilder().build_messages(state, [])[0]["content"]
