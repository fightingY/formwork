from __future__ import annotations

import threading

from minicc.core.loop import AgentLoop, AgentLoopResult
from minicc.core.multi_agent import ChildTask, MultiAgentError, MultiAgentManager
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage, NativeToolCall
from minicc.core.state import Observation, RunState, TrajectoryStep


class FakeLoop:
    def __init__(self, state: RunState, seen: list[tuple[str, list[dict[str, str]]]], gate: threading.Barrier | None = None):
        self.state = state
        self.seen = seen
        self.gate = gate

    def run(self, state: RunState) -> AgentLoopResult:
        self.seen.append((state.task_id or "", list(state.session_history)))
        if self.gate:
            self.gate.wait(timeout=2)
        state.status = "completed"
        state.final_answer = f"finding for {state.task_id}"
        state.metrics["turns"] = 1
        return AgentLoopResult(state, [])


class ParentProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, options: CompletionOptions | None = None):
        self.calls += 1
        if self.calls == 1:
            call = NativeToolCall(
                id="delegate-1",
                name="delegate",
                arguments='{"tasks":[{"id":"child","goal":"inspect","provider":"spawn"}]}',
            )
        else:
            call = NativeToolCall(id="final-1", name="final", arguments='{"answer":"workflow used"}')
        return ModelResponse(text="", raw={}, usage=ModelUsage(), latency_ms=0, tool_calls=(call,))


def test_fork_isolated_and_results_roll_up(tmp_path):
    parent = RunState.start("root", workspace_host_path=tmp_path)
    parent.session_history = [{"role": "user", "content": "prior"}]
    seen: list[tuple[str, list[dict[str, str]]]] = []

    def factory(state):
        return FakeLoop(state, seen)

    manager = MultiAgentManager(factory)
    trajectory = [TrajectoryStep(None, Observation(kind="command_result", message="done"))]
    results = manager.run(
        parent,
        [ChildTask("a", "inspect", provider="fork", capability_profile="scout")],
        parent_trajectory=trajectory,
    )

    assert results[0].status == "completed"
    assert results[0].facts[0].claim == "finding for a"
    assert parent.child_results[0]["task_id"] == "a"
    assert parent.facts[0]["claim"] == "finding for a"
    assert seen[0][1][0]["content"] == "prior"
    trajectory.clear()
    assert "Observation: done" in seen[0][1][-1]["content"]


def test_parallel_children_and_dependency_order(tmp_path):
    parent = RunState.start("root", workspace_host_path=tmp_path)
    barrier = threading.Barrier(2)
    seen: list[tuple[str, list[dict[str, str]]]] = []
    manager = MultiAgentManager(lambda state: FakeLoop(state, seen, barrier if state.task_id in {"a", "b"} else None), max_concurrent_children=2)
    results = manager.run(parent, [
        ChildTask("a", "a", capability_profile="scout"),
        ChildTask("b", "b", capability_profile="scout"),
        ChildTask("c", "c", depends_on=("a", "b")),
    ])
    assert [result.task_id for result in results] == ["a", "b", "c"]
    assert all(result.status == "completed" for result in results)


def test_validation_rejects_cycle_and_depth(tmp_path):
    parent = RunState.start("root", workspace_host_path=tmp_path)
    manager = MultiAgentManager(lambda state: FakeLoop(state, []), max_depth=0)
    try:
        manager.run(parent, [ChildTask("a", "a")])
    except MultiAgentError as exc:
        assert "depth" in str(exc)
    else:
        raise AssertionError("depth limit was not enforced")

    parent.depth = 0
    manager = MultiAgentManager(lambda state: FakeLoop(state, []))
    try:
        manager.run(parent, [ChildTask("a", "a", depends_on=("b",)), ChildTask("b", "b", depends_on=("a",))])
    except MultiAgentError as exc:
        assert "cycle" in str(exc)
    else:
        raise AssertionError("dependency cycle was not rejected")


def test_delegate_tool_is_executed_inside_parent_loop(tmp_path):
    parent = RunState.start("coordinate", workspace_host_path=tmp_path)
    child_manager = MultiAgentManager(lambda state: FakeLoop(state, []))
    loop = AgentLoop(
        ParentProvider(),
        executor=type("Executor", (), {"run": lambda *_args: None})(),
        multi_agent_manager=child_manager,
    )
    result = loop.run(parent)
    assert result.state.status == "completed"
    assert result.state.final_answer == "workflow used"
    assert result.state.child_results[0]["task_id"] == "child"
    assert result.trajectory[0].observation.kind == "command_result"
