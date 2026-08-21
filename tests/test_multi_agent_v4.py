from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from minicc.core.protocol import BashAction, DelegateAction, ProtocolError, parse_action
from minicc.multi_agent import (
    InProcessChildRunProvider,
    SubprocessChildRunProvider,
    WorkflowCoordinator,
)
from minicc.runtime import ChildCapabilities, ReadOnlyBashPolicy, WorkspaceWriteLease
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
    result = SubprocessChildRunProvider(timeout_sec=10).run(task, parent_run_id="p", root_run_id="r", workflow_id="w")
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
