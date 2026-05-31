from minicc.core.context import ContextBuilder, ContextConfig
from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState, TrajectoryStep


def test_context_builder_uses_cache_friendly_stable_prefix() -> None:
    builder = ContextBuilder()
    first = builder.build_messages(RunState.start("Run tests"), [])
    second = builder.build_messages(RunState.start("Write docs"), [])

    assert first[0]["role"] == "system"
    assert first[0]["content"] == second[0]["content"]
    assert "Run tests" in first[1]["content"]
    assert "Write docs" in second[1]["content"]
    assert "Run tests" not in first[0]["content"]


def test_context_builder_limits_recent_trajectory() -> None:
    builder = ContextBuilder(ContextConfig(recent_turns=2))
    state = RunState.start("Inspect repository")
    trajectory = [
        _step("pwd", "first"),
        _step("ls", "second"),
        _step("pytest -q", "third"),
    ]

    messages = builder.build_messages(state, trajectory)

    assert "pwd" not in messages[1]["content"]
    assert "ls" in messages[1]["content"]
    assert "pytest -q" in messages[1]["content"]


def test_context_builder_compacts_old_trajectory_once() -> None:
    builder = ContextBuilder(ContextConfig(max_prompt_chars=10, recent_turns=1, summary_max_chars=2_000))
    state = RunState.start("Debug failure")
    trajectory = [
        _step("pytest -q", "first failure", artifact_ids=["art_0001"]),
        _step("sed -n '1,120p' src/app.py", "inspected file"),
    ]

    builder.maybe_compact(state, trajectory)
    builder.maybe_compact(state, trajectory)

    assert state.metrics["context_compactions"] == 1
    assert state.metrics["context_compacted_steps"] == 1
    assert "pytest -q" in state.state_summary
    assert "art_0001" in state.state_summary

    messages = builder.build_messages(state, trajectory)

    assert "State summary" in messages[1]["content"]
    assert "sed -n" in messages[1]["content"]


def _step(command: str, message: str, artifact_ids: list[str] | None = None) -> TrajectoryStep:
    return TrajectoryStep(
        action=BashAction(command=command, purpose=message),
        observation=Observation(
            kind="command_result",
            exit_code=0,
            stdout_preview=message,
            artifact_ids=artifact_ids or [],
            message=message,
        ),
    )
