from minicc.core.context import ContextBuilder, ContextConfig
from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.memory.compaction import CompactionError, CompactionResult
from minicc.memory.feedback import FeedbackMemory
from minicc.skills.registry import SkillRegistry


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


def test_context_builder_uses_semantic_compactor_and_records_retention() -> None:
    class FakeCompactor:
        def compact(self, state, **kwargs):
            assert kwargs["source_steps"] == 1
            assert "src/app.py" in kwargs["trajectory_text"]
            return CompactionResult(summary="Root cause in src/app.py", input_chars=100, output_chars=24)

    builder = ContextBuilder(
        ContextConfig(
            max_prompt_chars=10,
            recent_turns=1,
            compaction_strategy="semantic",
            retention_markers=("src/app.py", "art_0001"),
        ),
        semantic_compactor=FakeCompactor(),
    )
    state = RunState.start("Debug failure")
    trajectory = [
        _step("sed -n '1,120p' src/app.py", "root cause", artifact_ids=["art_0001"]),
        _step("pytest -q", "passing"),
    ]

    builder.maybe_compact(state, trajectory)

    assert state.metrics["semantic_compaction_successes"] == 1
    assert state.metrics["semantic_compaction_failures"] == 0
    assert state.metrics["context_retention_rate"] == 1.0
    assert "src/app.py" in state.state_summary
    assert "art_0001" in state.state_summary


def test_semantic_summary_preserves_active_run_continuity() -> None:
    class FakeCompactor:
        def compact(self, state, **kwargs):
            return CompactionResult(summary="No open work identified", input_chars=10, output_chars=22)

    builder = ContextBuilder(
        ContextConfig(max_prompt_chars=10, recent_turns=1, compaction_strategy="semantic"),
        semantic_compactor=FakeCompactor(),
    )
    state = RunState.start("Implement the pending change")
    builder.maybe_compact(state, [_step("cat src/app.py", "inspected"), _step("cat tests", "inspected")])

    assert "Execution continuity (authoritative)" in state.state_summary
    assert "Implement the pending change" in state.state_summary
    assert "goal is not complete" in state.state_summary
    assert "next necessary action" in state.state_summary


def test_context_builder_marks_semantic_failure_and_falls_back() -> None:
    class FailingCompactor:
        def compact(self, state, **kwargs):
            raise CompactionError("invalid response")

    builder = ContextBuilder(
        ContextConfig(max_prompt_chars=10, recent_turns=1, compaction_strategy="semantic"),
        semantic_compactor=FailingCompactor(),
    )
    state = RunState.start("Debug failure")

    builder.maybe_compact(state, [_step("pytest -q", "failed"), _step("rg bug", "found")])

    assert state.metrics["context_compactions"] == 1
    assert state.metrics["semantic_compaction_successes"] == 0
    assert state.metrics["semantic_compaction_failures"] == 1
    assert "pytest -q" in state.state_summary


def test_context_builder_records_prompt_length_distribution() -> None:
    builder = ContextBuilder()
    state = RunState.start("Inspect")

    first = builder.build_messages(state, [])
    second = builder.build_messages(state, [_step("pwd", "ok")])

    lengths = [sum(len(message["content"]) for message in item) for item in (first, second)]
    assert state.metrics["prompt_char_samples"] == 2
    assert state.metrics["prompt_chars_total"] == sum(lengths)
    assert state.metrics["prompt_chars_max"] == max(lengths)
    assert state.metrics["prompt_chars_mean"] == sum(lengths) / 2


def test_context_builder_injects_skill_catalog_and_feedback_memory(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "repo-inspection"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: repo-inspection\ndescription: Inspect repository structure.\n---\n",
        encoding="utf-8",
    )
    memory_path = tmp_path / "feedback_rules.jsonl"
    memory_path.write_text(
        '{"id":"mem_1","type":"caution","rule":"Use rg before broad file reads."}\n',
        encoding="utf-8",
    )
    builder = ContextBuilder(
        skill_registry=SkillRegistry(tmp_path / "skills"),
        feedback_memory=FeedbackMemory(memory_path),
    )

    messages = builder.build_messages(RunState.start("Use rg to inspect repository files"), [])

    assert "repo-inspection: Inspect repository structure." in messages[1]["content"]
    assert "caution: Use rg before broad file reads." in messages[1]["content"]


def test_context_builder_adds_budget_pressure_after_thresholds() -> None:
    builder = ContextBuilder()
    state = RunState.start("Finish task")
    state.metrics["max_turns"] = 10
    state.metrics["turns"] = 6

    at_sixty = builder.build_messages(state, [])[1]["content"]
    state.metrics["turns"] = 8
    at_eighty = builder.build_messages(state, [])[1]["content"]

    assert "Converge now" in at_sixty
    assert "Stop exploring" in at_eighty


def test_context_builder_adds_io_repetition_guard() -> None:
    builder = ContextBuilder()
    state = RunState.start("Finish the pending patch")
    state.metrics["repeated_file_reads"] = 2
    state.metrics["repeated_searches"] = 1

    content = builder.build_messages(state, [])[1]["content"]

    assert "I/O repetition guard" in content
    assert "make the smallest required patch" in content


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
