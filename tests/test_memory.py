import json

import pytest

from minicc.core.context import ContextBuilder
from minicc.core.protocol import MemoryReference
from minicc.core.provider import ModelResponse, ModelUsage
from minicc.core.state import RunState
from minicc.memory.compaction import SemanticCompactor
from minicc.memory.feedback import FeedbackMemory
from minicc.memory.working import (
    WorkingMemoryError,
    attach_working_memory,
    ground_memory_references,
    working_memory_context,
    write_working_memory_snapshot,
)
from minicc.sandbox.workspace import prepare_run_workspace
from minicc.trace.recorder import TraceRecorder


def test_feedback_memory_loads_and_filters_rules(tmp_path) -> None:
    memory_path = tmp_path / "feedback_rules.jsonl"
    memory_path.write_text(
        "\n".join(
            [
                json.dumps({"id": "mem_1", "type": "prefer", "rule": "Prefer pytest for python tests."}),
                json.dumps({"id": "mem_2", "type": "never", "rule": "Never delete source files."}),
            ]
        ),
        encoding="utf-8",
    )

    memory = FeedbackMemory(memory_path)
    rules = memory.relevant_rules("Run python pytest", limit=1)

    assert len(rules) == 1
    assert rules[0].id == "mem_1"
    assert "prefer: Prefer pytest for python tests." in memory.context_text("Run python pytest")


def test_feedback_memory_omits_unrelated_rules(tmp_path) -> None:
    memory_path = tmp_path / "feedback_rules.jsonl"
    memory_path.write_text(
        json.dumps({"id": "mem_1", "type": "prefer", "rule": "Prefer pytest for python tests."}),
        encoding="utf-8",
    )

    memory = FeedbackMemory(memory_path)

    assert memory.relevant_rules("Write docs") == []
    assert memory.context_text("Write docs") == ""


def test_memory_layers_have_separate_owners_and_lifetimes(tmp_path) -> None:
    feedback_path = tmp_path / "feedback.jsonl"
    feedback_path.write_text(
        '{"id":"rule-1","type":"prefer","rule":"run focused tests"}\n',
        encoding="utf-8",
    )
    first = RunState.start("inspect service")
    first.state_summary = "short-term run state"
    second = RunState.start("follow up")

    assert second.state_summary == ""
    assert second.working_memory == []
    assert first.run_id != second.run_id
    assert FeedbackMemory(feedback_path).load_rules()[0].scope == "project"


def test_grounded_working_memory_is_explicitly_attached_and_injected_once(tmp_path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "contract.txt").write_text("service=atlas\nport=8142\nmode=strict\n", encoding="utf-8")
    runs_root = tmp_path / "runs"
    source_workspace = prepare_run_workspace(fixture, run_id="source-run", runs_root=runs_root)
    source = RunState.start(
        "inspect the service contract",
        workspace_host_path=source_workspace.workspace_dir,
        run_dir=source_workspace.run_dir,
        artifacts_dir=source_workspace.artifacts_dir,
    )
    source.run_id = "source-run"
    accepted, rejected = ground_memory_references(
        source,
        [MemoryReference("contract.txt", 1, 3)],
    )
    assert rejected == []
    source.memory_references = accepted
    snapshot_path = write_working_memory_snapshot(source)
    assert snapshot_path == source_workspace.run_dir / "working_memory.json"

    follow_workspace = prepare_run_workspace(fixture, run_id="follow-run", runs_root=runs_root)
    follow = RunState.start(
        "update the atlas service without rereading its contract",
        workspace_host_path=follow_workspace.workspace_dir,
        run_dir=follow_workspace.run_dir,
        artifacts_dir=follow_workspace.artifacts_dir,
    )
    follow.run_id = "follow-run"
    attach_working_memory(follow, "source-run", runs_root=runs_root)
    trace = TraceRecorder()
    builder = ContextBuilder(trace=trace)

    first_messages = builder.build_messages(follow, [])
    builder.build_messages(follow, [])

    assert follow.working_memory_source_run_id == "source-run"
    assert "port=8142" in working_memory_context(follow)
    assert "port=8142" in first_messages[1]["content"]
    assert follow.metrics["working_memory_items_injected"] == 1
    assert [event["event"] for event in trace.events].count("working_memory_injected") == 1


def test_working_memory_rejects_tampering_and_project_drift(tmp_path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "contract.txt").write_text("port=8142\n", encoding="utf-8")
    runs_root = tmp_path / "runs"
    source_workspace = prepare_run_workspace(fixture, run_id="source-run", runs_root=runs_root)
    source = RunState.start(
        "inspect",
        workspace_host_path=source_workspace.workspace_dir,
        run_dir=source_workspace.run_dir,
    )
    source.run_id = "source-run"
    source.memory_references, _ = ground_memory_references(
        source,
        [MemoryReference("contract.txt", 1, 1)],
    )
    snapshot = write_working_memory_snapshot(source)
    assert snapshot is not None

    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["items"][0]["excerpt"] = "port=9999"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    same_workspace = prepare_run_workspace(fixture, run_id="same-run", runs_root=runs_root)
    same = RunState.start(
        "follow up",
        run_dir=same_workspace.run_dir,
        workspace_host_path=same_workspace.workspace_dir,
    )
    with pytest.raises(WorkingMemoryError, match="integrity"):
        attach_working_memory(same, "source-run", runs_root=runs_root)
    assert same.working_memory == []

    source.memory_references, _ = ground_memory_references(
        source,
        [MemoryReference("contract.txt", 1, 1)],
    )
    write_working_memory_snapshot(source)
    changed_fixture = tmp_path / "changed"
    changed_fixture.mkdir()
    (changed_fixture / "contract.txt").write_text("port=9000\n", encoding="utf-8")
    changed_workspace = prepare_run_workspace(changed_fixture, run_id="changed-run", runs_root=runs_root)
    changed = RunState.start(
        "follow up",
        run_dir=changed_workspace.run_dir,
        workspace_host_path=changed_workspace.workspace_dir,
    )
    with pytest.raises(WorkingMemoryError, match="project snapshot"):
        attach_working_memory(changed, "source-run", runs_root=runs_root)
    assert changed.metrics["working_memory_items_injected"] == 0


def test_working_memory_rejects_live_source_file_drift(tmp_path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "contract.txt").write_text("port=8142\n", encoding="utf-8")
    runs_root = tmp_path / "runs"
    source_workspace = prepare_run_workspace(fixture, run_id="source-run", runs_root=runs_root)
    source = RunState.start(
        "inspect",
        workspace_host_path=source_workspace.workspace_dir,
        run_dir=source_workspace.run_dir,
    )
    source.run_id = "source-run"
    source.memory_references, _ = ground_memory_references(
        source,
        [MemoryReference("contract.txt", 1, 1)],
    )
    write_working_memory_snapshot(source)
    follow_workspace = prepare_run_workspace(fixture, run_id="follow-run", runs_root=runs_root)
    (follow_workspace.workspace_dir / "contract.txt").write_text("port=9000\n", encoding="utf-8")
    follow = RunState.start(
        "follow up",
        run_dir=follow_workspace.run_dir,
        workspace_host_path=follow_workspace.workspace_dir,
    )

    with pytest.raises(WorkingMemoryError, match="source evidence changed"):
        attach_working_memory(follow, "source-run", runs_root=runs_root)

    assert follow.working_memory == []


def test_working_memory_rejects_unverifiable_reference(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "short.txt").write_text("one line\n", encoding="utf-8")
    state = RunState.start("inspect", workspace_host_path=workspace)

    accepted, rejected = ground_memory_references(
        state,
        [MemoryReference("short.txt", 1, 2), MemoryReference("missing.txt", 1, 1)],
    )

    assert accepted == []
    assert [item["reason"] for item in rejected] == [
        "line_range_out_of_bounds",
        "source_is_not_a_regular_file",
    ]


def test_semantic_compactor_requests_structured_summary_and_tracks_separate_usage() -> None:
    class Provider:
        def complete(self, messages, *, options=None):
            assert options.json_mode is True
            assert options.max_tokens == 2048
            assert "src/app.py" in messages[1]["content"]
            return ModelResponse(
                text='{"summary":"Root cause: src/app.py"}',
                raw={},
                usage=ModelUsage(prompt_tokens=80, completion_tokens=12),
                latency_ms=7,
            )

    state = RunState.start("debug")
    result = SemanticCompactor(Provider()).compact(
        state,
        trajectory_text="Read src/app.py and found the root cause.",
        retention_markers=("src/app.py",),
        source_steps=2,
    )

    assert result.summary == "Root cause: src/app.py"
    assert state.metrics["semantic_compaction_requests"] == 1
    assert state.metrics["semantic_compaction_prompt_tokens"] == 80
    assert state.metrics["prompt_tokens"] == 0
