import json
from dataclasses import dataclass

import pytest

from minicc.core.checkpoint import (
    AmbiguousExecutionError,
    CheckpointDriftError,
    CheckpointManager,
)
from minicc.core.loop import AgentLoop, LoopConfig
from minicc.core.protocol import BashAction
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.state import (
    Observation,
    RunState,
    TrajectoryStep,
    save_run_state,
    trajectory_step_from_dict,
)
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff
from minicc.trace.recorder import TraceRecorder


@dataclass
class SequenceProvider:
    responses: list[str | BaseException]

    def complete(self, messages, *, options: CompletionOptions | None = None) -> ModelResponse:
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return ModelResponse(text=response, raw={}, usage=ModelUsage(), latency_ms=1)


class ScenarioExecutor:
    def __init__(self, workspace, *, verification_fails: bool = False) -> None:
        self.workspace = workspace
        self.verification_fails = verification_fails
        self.commands: list[str] = []

    def run(self, action: BashAction, state: RunState) -> Observation:
        self.commands.append(action.command)
        if action.command == "write-change":
            (self.workspace / "app.py").write_text("changed\n", encoding="utf-8")
            return Observation(kind="command_result", exit_code=0, message="change written")
        if action.command == "fix-change":
            (self.workspace / "app.py").write_text("fixed\n", encoding="utf-8")
            return Observation(kind="command_result", exit_code=0, message="change fixed")
        if action.command == "verify" and self.verification_fails:
            return Observation(kind="command_error", exit_code=1, message="verification failed")
        return Observation(kind="command_result", exit_code=0, message="verification passed")


def test_checkpoint_round_trips_state_trajectory_and_workspace(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    (state.workspace_host_path / "app.py").write_text("changed\n", encoding="utf-8")
    trajectory = [
        TrajectoryStep(
            action=BashAction(command="write-change"),
            observation=Observation(kind="command_result", exit_code=0, message="done"),
            state_snapshot="Budget status: 2 model turn(s) remain.",
        )
    ]

    manager.create(state, trajectory, reason="action_completed")
    restored = manager.restore_latest(state.run_id)

    assert restored.state.run_id == state.run_id
    assert restored.state.resume_count == 1
    assert restored.trajectory == trajectory
    assert restored.reason == "action_completed"


def test_legacy_checkpoint_step_without_snapshot_remains_loadable() -> None:
    step = trajectory_step_from_dict(
        {
            "action": {"type": "bash", "command": "pwd", "timeout_sec": 60, "purpose": ""},
            "observation": {
                "kind": "command_result",
                "exit_code": 0,
                "stdout_preview": "",
                "stderr_preview": "",
                "artifact_ids": [],
                "message": "ok",
                "duration_ms": 0,
            },
        }
    )

    assert step.action == BashAction(command="pwd")
    assert step.state_snapshot == ""


def test_checkpoint_create_and_restore_write_trace_events(tmp_path) -> None:
    state, _ = _checkpoint_state(tmp_path)
    trace = TraceRecorder(state.run_dir / "trace.jsonl")
    manager = CheckpointManager(state.run_dir, trace=trace)

    manager.create(state, [], reason="run_started")
    manager.restore_latest(state.run_id)

    assert [event["event"] for event in trace.events] == [
        "checkpoint_created",
        "checkpoint_restored",
    ]


def test_checkpoint_rejects_workspace_drift(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    manager.create(state, [], reason="run_started")
    (state.workspace_host_path / "drift.txt").write_text("external change", encoding="utf-8")

    with pytest.raises(CheckpointDriftError, match="Workspace content changed"):
        manager.restore_latest(state.run_id)


def test_checkpoint_rejects_wrong_run_pointer(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    manager.create(state, [], reason="run_started")
    pointer_path = state.run_dir / "checkpoints" / "latest.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["run_id"] = "different-run"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(CheckpointDriftError, match="different run"):
        manager.restore_latest(state.run_id)


def test_checkpoint_blocks_ambiguous_action_replay(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    manager.create(state, [], reason="run_started")
    state.execution_journal.append(
        {"execution_id": "execution-0001", "status": "started", "command": "write-change"}
    )
    save_run_state(state)

    with pytest.raises(AmbiguousExecutionError, match="automatic replay is blocked"):
        manager.restore_latest(state.run_id)


def test_resume_before_modification_preserves_empty_trajectory(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    first_loop = AgentLoop(
        SequenceProvider([KeyboardInterrupt()]),
        ScenarioExecutor(state.workspace_host_path),
        session=SessionManager(),
        checkpoint_manager=manager,
    )

    with pytest.raises(KeyboardInterrupt):
        first_loop.run(state)

    restored = manager.restore_latest(state.run_id)
    result = AgentLoop(
        SequenceProvider(['{"type":"final","answer":"done"}']),
        ScenarioExecutor(state.workspace_host_path),
        session=SessionManager(),
        checkpoint_manager=manager,
    ).run(restored.state, restored.trajectory)

    assert result.state.status == "completed"
    assert restored.trajectory == []


def test_resume_after_modification_does_not_repeat_completed_action(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    first_executor = ScenarioExecutor(state.workspace_host_path)
    first_loop = AgentLoop(
        SequenceProvider(
            [
                '{"type":"bash","command":"write-change"}',
                KeyboardInterrupt(),
            ]
        ),
        first_executor,
        session=SessionManager(),
        checkpoint_manager=manager,
    )

    with pytest.raises(KeyboardInterrupt):
        first_loop.run(state)

    restored = manager.restore_latest(state.run_id)
    resumed_executor = ScenarioExecutor(state.workspace_host_path)
    result = AgentLoop(
        SequenceProvider(
            [
                '{"type":"bash","command":"verify"}',
                '{"type":"final","answer":"done"}',
            ]
        ),
        resumed_executor,
        session=SessionManager(),
        checkpoint_manager=manager,
    ).run(restored.state, restored.trajectory)

    assert result.state.status == "completed"
    assert first_executor.commands == ["write-change"]
    assert resumed_executor.commands == ["verify"]
    assert (state.workspace_host_path / "app.py").read_text(encoding="utf-8") == "changed\n"


def test_resume_after_failed_verification_preserves_failure_observation(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    first_executor = ScenarioExecutor(state.workspace_host_path, verification_fails=True)
    first_loop = AgentLoop(
        SequenceProvider(
            [
                '{"type":"bash","command":"write-change"}',
                '{"type":"bash","command":"verify"}',
                KeyboardInterrupt(),
            ]
        ),
        first_executor,
        session=SessionManager(),
        checkpoint_manager=manager,
    )

    with pytest.raises(KeyboardInterrupt):
        first_loop.run(state)

    restored = manager.restore_latest(state.run_id)
    assert restored.trajectory[-1].observation.kind == "command_error"
    resumed_executor = ScenarioExecutor(state.workspace_host_path)
    result = AgentLoop(
        SequenceProvider(
            [
                '{"type":"bash","command":"fix-change"}',
                '{"type":"bash","command":"verify"}',
                '{"type":"final","answer":"done"}',
            ]
        ),
        resumed_executor,
        session=SessionManager(),
        checkpoint_manager=manager,
    ).run(restored.state, restored.trajectory)

    assert result.state.status == "completed"
    assert first_executor.commands == ["write-change", "verify"]
    assert resumed_executor.commands == ["fix-change", "verify"]
    assert (state.workspace_host_path / "app.py").read_text(encoding="utf-8") == "fixed\n"


def test_controlled_interrupt_creates_resumable_checkpoint(tmp_path) -> None:
    state, manager = _checkpoint_state(tmp_path)
    result = AgentLoop(
        SequenceProvider(['{"type":"bash","command":"write-change"}']),
        ScenarioExecutor(state.workspace_host_path),
        session=SessionManager(),
        checkpoint_manager=manager,
        config=LoopConfig(interrupt_after_steps=1),
    ).run(state)

    assert result.state.status == "interrupted"
    restored = manager.restore_latest(state.run_id)
    assert restored.reason == "interrupted_finalized"
    assert len(restored.trajectory) == 1
    assert restored.trajectory[0].action == BashAction(command="write-change")


def _step(command, kind, *, exit_code=0):
    action = BashAction(command=command) if command is not None else None
    return TrajectoryStep(
        action=action,
        observation=Observation(kind=kind, exit_code=exit_code, message=kind),
    )


@pytest.mark.parametrize(
    ("scenario", "trajectory"),
    [
        ("before_modification", []),
        ("after_repository_read", [_step("read", "command_result")]),
        ("after_modification", [_step("write-change", "command_result")]),
        ("after_failed_verification", [_step("verify", "command_error", exit_code=1)]),
        ("after_successful_verification", [_step("verify", "command_result")]),
        ("after_policy_denial", [_step("pip install pytest", "policy_violation")]),
        ("after_protocol_error", [_step(None, "protocol_error")]),
        ("after_timeout", [_step("slow-command", "timeout")]),
        ("after_multiple_actions", [_step("read", "command_result"), _step("write-change", "command_result")]),
        ("after_approval_observation", [_step("approved-command", "approval_result")]),
    ],
)
def test_checkpoint_resume_matrix_completes_ten_state_scenarios(tmp_path, scenario, trajectory) -> None:
    state, manager = _git_checkpoint_state(tmp_path, scenario)
    if scenario in {"after_modification", "after_multiple_actions"}:
        (state.workspace_host_path / "app.py").write_text("changed\n", encoding="utf-8")
    expected_diff = write_workspace_diff(state.workspace_host_path, state.artifacts_dir).read_text(encoding="utf-8")
    state.state_summary = f"scenario={scenario}"
    manager.create(state, trajectory, reason=scenario)

    restored = manager.restore_latest(state.run_id)
    result = AgentLoop(
        SequenceProvider(['{"type":"final","answer":"resumed"}']),
        ScenarioExecutor(state.workspace_host_path),
        session=SessionManager(),
        checkpoint_manager=manager,
    ).run(restored.state, restored.trajectory)

    assert result.state.status == "completed"
    assert result.state.final_answer == "resumed"
    assert result.state.state_summary == f"scenario={scenario}"
    assert len(restored.trajectory) == len(trajectory)
    actual_diff = write_workspace_diff(state.workspace_host_path, state.artifacts_dir).read_text(encoding="utf-8")
    assert actual_diff == expected_diff


def _checkpoint_state(tmp_path):
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    (workspace / "app.py").write_text("original\n", encoding="utf-8")
    state = RunState.start(
        "checkpoint scenario",
        run_dir=run_dir,
        workspace_host_path=workspace,
        artifacts_dir=artifacts,
    )
    save_run_state(state)
    return state, CheckpointManager(run_dir)


def _git_checkpoint_state(tmp_path, scenario):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("original\n", encoding="utf-8")
    workspace = prepare_run_workspace(source, run_id=scenario, runs_root=tmp_path / "runs")
    state = RunState.start(
        f"checkpoint matrix {scenario}",
        run_dir=workspace.run_dir,
        workspace_host_path=workspace.workspace_dir,
        artifacts_dir=workspace.artifacts_dir,
    )
    state.run_id = scenario
    save_run_state(state)
    return state, CheckpointManager(workspace.run_dir)
