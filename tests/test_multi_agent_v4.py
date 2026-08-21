from __future__ import annotations

import json
import threading
import time
from io import StringIO
from pathlib import Path

import pytest

from minicc.core.protocol import BashAction, DelegateAction, ProtocolError, parse_action
from minicc.multi_agent import (
    ChildResult,
    InProcessChildRunProvider,
    SubprocessChildRunProvider,
    WorkflowCoordinator,
    childrun_main,
    reviewer_loop,
    standard_scout_planner_worker,
    validate_delegate,
)
from minicc.runtime import (
    AgentRuntime,
    ChildCapabilities,
    ReadOnlyBashPolicy,
    WorkspaceWriteLease,
    workspace_fingerprint,
)
from minicc.trace.transcript import TranscriptProjector


def test_delegate_schema_rejects_duplicate_and_cycle() -> None:
    with pytest.raises(ProtocolError):
        parse_action('{"type":"delegate","tasks":[{"id":"x","role":"scout","goal":"a","capability_profile":"scout"},{"id":"x","role":"scout","goal":"b","capability_profile":"scout"}]}')
    with pytest.raises(ProtocolError):
        parse_action('{"type":"delegate","tasks":[{"id":"a","role":"scout","goal":"a","capability_profile":"scout","depends_on":["b"]},{"id":"b","role":"scout","goal":"b","capability_profile":"scout","depends_on":["a"]}]}')


def test_read_only_contract_and_lease() -> None:
    policy = ReadOnlyBashPolicy()
    assert policy.decide(BashAction("git status")).allowed
    assert not policy.decide(BashAction("echo x > output.txt")).allowed
    assert not policy.decide(BashAction("git checkout -- file.py")).allowed
    assert ChildCapabilities.for_profile("scout").tools == frozenset({"read", "bash"})
    lease = WorkspaceWriteLease()
    epoch = lease.acquire("r1", "w1")
    assert epoch is not None
    assert lease.acquire("r2", "w2") is None
    assert lease.allows("r1", "w1", epoch)
    assert lease.release("r1", "w1", epoch)


def test_workflow_parallel_and_dependency_order() -> None:
    starts: list[str] = []
    lock = threading.Lock()

    def handler(*, task, **_):
        with lock:
            starts.append(task.id)
        time.sleep(0.02)
        return {"summary": task.id}

    action = parse_action(
        '{"type":"delegate","tasks":['
        '{"id":"a","role":"scout","goal":"a","capability_profile":"scout"},'
        '{"id":"b","role":"scout","goal":"b","capability_profile":"scout"},'
        '{"id":"p","role":"planner","goal":"p","capability_profile":"planner","depends_on":["a","b"]}'
        ']}'
    )
    assert isinstance(action, DelegateAction)
    result = WorkflowCoordinator(InProcessChildRunProvider(handler), max_concurrent_children=2).execute(action, parent_run_id="root")
    assert result.status == "completed"
    assert result.max_concurrent == 2
    assert starts.index("p") > starts.index("a") and starts.index("p") > starts.index("b")


def test_subprocess_childrun_and_transcript(tmp_path: Path) -> None:
    task = parse_action('{"type":"delegate","tasks":[{"id":"s","role":"scout","goal":"inspect","capability_profile":"scout"}]}').tasks[0]  # type: ignore[union-attr]
    result = SubprocessChildRunProvider(timeout_sec=10, use_model=False).run(task, parent_run_id="p", root_run_id="r", workflow_id="w")
    assert result.status == "completed"
    projector = TranscriptProjector(root_run_id="r")
    projector.project([
        {"event": "action_started", "run_id": "r", "action": {"type": "delegate", "intent": "inspect"}},
        {"event": "workflow_summary_observation", "run_id": "r", "workflow_id": "w", "observation": {"status": "completed"}},
    ])
    json_path, md_path = projector.write(tmp_path)
    first = json.loads(json_path.read_text(encoding="utf-8").splitlines()[0])
    assert first["intent_kind"] == "model_intent"
    assert "Summary" in md_path.read_text(encoding="utf-8")


def test_in_process_provider_normalizes_cancel_dict_and_exception() -> None:
    task = parse_action('{"type":"delegate","tasks":[{"id":"s","role":"scout","goal":"inspect","capability_profile":"scout"}]}').tasks[0]  # type: ignore[union-attr]
    cancelled = threading.Event()
    cancelled.set()
    result = InProcessChildRunProvider().run(task, parent_run_id="p", root_run_id="r", workflow_id="w", cancel=cancelled)
    assert result.status == "cancelled"

    failed = InProcessChildRunProvider(lambda **_: 1 / 0).run(task, parent_run_id="p", root_run_id="r", workflow_id="w")
    assert failed.failure and failed.failure["code"] == "CHILD_ERROR"
    raw = InProcessChildRunProvider(lambda **_: {"status": "completed", "findings": ["ok"]}).run(task, parent_run_id="p", root_run_id="r", workflow_id="w")
    assert raw.status == "completed" and raw.findings == ("ok",)
    assert ChildResult("t", "c", "completed", "scout").to_dict()["findings"] == []


def test_workflow_join_any_cancellation_and_factory_helpers() -> None:
    action = standard_scout_planner_worker(scout_goals=["a", "b"], planner_goal="plan", worker_goal="work")
    assert [task.id for task in action.tasks] == ["scout-1", "scout-2", "planner", "worker"]
    assert len(reviewer_loop(worker_goal="work", reviewer_goal="review", max_iterations=2)) == 2

    join_any = DelegateAction((action.tasks[0], action.tasks[1]), join="any")
    result = WorkflowCoordinator(InProcessChildRunProvider(), max_concurrent_children=1).execute(join_any, parent_run_id="p")
    assert result.status == "failed"

    cancel = threading.Event()
    cancel.set()
    result = WorkflowCoordinator(InProcessChildRunProvider()).execute(action, parent_run_id="p", cancel=cancel)
    assert all(item.status == "aborted_before_dispatch" for item in result.results)


def test_delegate_validation_and_childrun_protocol_errors() -> None:
    with pytest.raises(ValueError, match="positive"):
        WorkflowCoordinator(InProcessChildRunProvider(), max_concurrent_children=0)
    with pytest.raises(ValueError, match="maximum workflow depth"):
        validate_delegate(DelegateAction(()), depth=4)
    with pytest.raises(ValueError, match="capability above"):
        validate_delegate(DelegateAction((parse_action('{"type":"delegate","tasks":[{"id":"x","role":"worker","goal":"x","capability_profile":"root"}]}').tasks[0],)), parent_profile="scout")  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="worker capability"):
        validate_delegate(DelegateAction((parse_action('{"type":"delegate","tasks":[{"id":"x","role":"worker","goal":"x","capability_profile":"root"}]}').tasks[0],)))  # type: ignore[union-attr]

    invalid = StringIO("not json\n")
    output = StringIO()
    assert childrun_main(invalid, output) == 2
    assert "INVALID_REQUEST" in output.getvalue()


def test_runtime_authorization_and_fingerprint(tmp_path: Path) -> None:
    call = parse_action('{"type":"tool_calls","calls":[{"id":"r","tool":"read","arguments":{"path":"a"}}]}').calls[0]  # type: ignore[union-attr]
    write = parse_action('{"type":"tool_calls","calls":[{"id":"w","tool":"write","arguments":{"path":"a","content":"x"}}]}').calls[0]  # type: ignore[union-attr]
    read_only = AgentRuntime(ChildCapabilities.for_profile("scout"))
    assert read_only.authorize(call).allowed
    assert not read_only.authorize(write).allowed
    before = workspace_fingerprint(tmp_path)
    (tmp_path / "a.txt").write_text("content", encoding="utf-8")
    assert workspace_fingerprint(tmp_path) != before
    assert ChildCapabilities.for_profile("worker").can_delegate is False
    with pytest.raises(ValueError, match="Unknown capability"):
        ChildCapabilities.for_profile("unknown")
