import pytest

from minicc.core.context import STABLE_PREFIX, ContextBuilder, ContextConfig, state_snapshot_text
from minicc.core.protocol import BashAction
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.memory.compaction import CompactionError, CompactionResult
from minicc.memory.feedback import FeedbackMemory
from minicc.skills.registry import SkillRegistry
from minicc.trace.recorder import TraceRecorder


def test_context_builder_uses_cache_friendly_stable_prefix() -> None:
    builder = ContextBuilder()
    first = builder.build_messages(RunState.start("Run tests"), [])
    second = builder.build_messages(RunState.start("Write docs"), [])

    assert first[0]["role"] == "system"
    assert first[0]["content"] == second[0]["content"]
    assert "Run tests" in first[1]["content"]
    assert "Write docs" in second[1]["content"]
    assert "Run tests" not in first[0]["content"]


def test_stable_prefix_requires_safe_action_economy_and_final_verification() -> None:
    assert "For code-modification goals, use the fewest safe model turns" in STABLE_PREFIX
    assert "skip redundant pre-change verification" in STABLE_PREFIX
    assert "authoritative verification" in STABLE_PREFIX


def test_rebuild_layout_keeps_v21_message_text_and_order() -> None:
    state = RunState.start("Finish patch")
    state.metrics["max_turns"] = 10
    state.metrics["turns"] = 6
    state.metrics["repeated_file_reads"] = 1
    state.constraints = ["Only edit src/app.py"]
    state.state_summary = "Root cause found"
    state.open_questions = ["Confirm behavior"]
    state.approval_question = "Approve command?"
    state.last_observation = Observation(kind="command_result", exit_code=0, message="inspected")
    trajectory = [_step("pwd", "first")]

    messages = ContextBuilder().build_messages(state, trajectory)

    expected_context = [
        "Goal: Finish patch",
        "Run status: running",
        "Budget status: 4 model turn(s) remain. Converge now: "
        "finish the smallest correct change, verify once, and avoid repeated inspection.",
        "I/O repetition guard: the same file/search action has already been repeated "
        "(1 file read(s), 0 search(es)). "
        "Do not repeat it again; make the smallest required patch or run the authoritative verification now.",
        "Constraints:\n- Only edit src/app.py",
        "State summary:\nRoot cause found",
        "Open questions:\n- Confirm behavior",
        "Pending approval question:\nApprove command?",
        "Last observation:\nkind=command_result\nexit_code=0\nmessage=inspected\n"
        "stdout_preview=\nstderr_preview=",
        "Recent trajectory:\nStep 1\n"
        'Action: {"type": "bash", "command": "pwd", "timeout_sec": 60, "purpose": "first"}\n'
        "Observation: kind=command_result\nexit_code=0\nmessage=first\n"
        "stdout_preview=first\nstderr_preview=",
    ]
    assert messages == [
        {"role": "system", "content": STABLE_PREFIX},
        {"role": "user", "content": "\n\n".join(expected_context)},
    ]


def test_context_builder_rejects_unknown_prompt_layout() -> None:
    with pytest.raises(ValueError, match="prompt_layout"):
        ContextBuilder(ContextConfig(prompt_layout="unknown"))  # type: ignore[arg-type]


def test_append_layout_preserves_complete_message_prefix_until_window_moves() -> None:
    trace = TraceRecorder()
    builder = ContextBuilder(ContextConfig(prompt_layout="append"), trace=trace)
    state = RunState.start("Inspect repository")
    first_step = _step("pwd", "first")
    second_step = _step("ls", "second")

    initial = builder.build_messages(state, [])
    initial_hash = state.metrics["stable_prefix_hash"]
    after_first = builder.build_messages(state, [first_step])
    after_first_hash = state.metrics["stable_prefix_hash"]
    after_second = builder.build_messages(state, [first_step, second_step])

    assert after_first[: len(initial)] == initial
    assert after_second[: len(after_first)] == after_first
    assert [message["role"] for message in after_second] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert state.metrics["prompt_layout"] == "append"
    assert initial_hash == after_first_hash == state.metrics["stable_prefix_hash"]
    assert state.metrics["stable_prefix_message_count"] == 2
    assert state.metrics["stable_prefix_estimated_tokens"] > 0
    profile = trace.events[-1]["prefix_profile"]
    assert profile["token_count_kind"] == "estimated"
    assert profile["sha256"] == state.metrics["stable_prefix_hash"]
    assert "content" not in profile


def test_append_layout_resets_only_after_stable_prefix_when_recent_window_moves() -> None:
    builder = ContextBuilder(ContextConfig(prompt_layout="append", recent_turns=1))
    state = RunState.start("Inspect repository")
    first_step = _step("pwd", "first")
    second_step = _step("ls", "second")

    after_first = builder.build_messages(state, [first_step])
    after_second = builder.build_messages(state, [first_step, second_step])

    assert after_second[:2] == after_first[:2]
    assert after_second[: len(after_first)] != after_first


def test_epoch_layout_preserves_complete_prefix_after_recent_window_would_move() -> None:
    trace = TraceRecorder()
    builder = ContextBuilder(
        ContextConfig(prompt_layout="epoch", recent_turns=1),
        trace=trace,
    )
    state = RunState.start("Inspect repository")
    first_step = _step("pwd", "first")
    second_step = _step("ls", "second")

    after_first = builder.build_messages(state, [first_step])
    after_second = builder.build_messages(state, [first_step, second_step])

    assert after_second[: len(after_first)] == after_first
    assert state.metrics["cache_prefix_epoch"] == 1
    assert state.metrics["cache_prefix_exact_append_requests"] == 1
    assert trace.events[-1]["prefix_profile"]["previous_request_is_exact_prefix"] is True
    assert trace.events[-1]["prefix_profile"]["prefix_reset_reason"] == "exact_append"


def test_epoch_layout_starts_new_prefix_epoch_after_compaction() -> None:
    trace = TraceRecorder()
    builder = ContextBuilder(
        ContextConfig(
            prompt_layout="epoch",
            recent_turns=1,
            max_prompt_chars=10,
            summary_max_chars=2_000,
        ),
        trace=trace,
    )
    state = RunState.start("Inspect repository")
    trajectory = [_step("pwd", "first"), _step("ls", "second")]

    builder.build_messages(state, trajectory)
    builder.maybe_compact(state, trajectory)
    builder.build_messages(state, trajectory)

    assert state.metrics["context_compactions"] == 1
    assert state.metrics["cache_prefix_epoch"] == 2
    assert state.metrics["cache_prefix_reset_requests"] == 1
    assert state.metrics["cache_prefix_reset_reason"] == "compaction_epoch_rollover"


def test_epoch_compaction_uses_hysteresis_and_does_not_reset_again_next_turn() -> None:
    builder = ContextBuilder(
        ContextConfig(
            prompt_layout="epoch",
            recent_turns=6,
            max_prompt_chars=3_000,
            summary_max_chars=500,
        )
    )
    state = RunState.start("Inspect repository")
    trajectory = [
        _step(f"command-{index}", "x" * 600)
        for index in range(8)
    ]

    builder.build_messages(state, trajectory)
    builder.maybe_compact(state, trajectory)
    after_rollover = builder.build_messages(state, trajectory)
    trajectory.append(_step("one-more-command", "y" * 100))
    builder.maybe_compact(state, trajectory)
    next_prompt = builder.build_messages(state, trajectory)

    assert state.metrics["context_compactions"] == 1
    assert state.metrics["context_compaction_target_ratio"] == 0.65
    assert state.metrics["context_compacted_steps"] > 2
    assert next_prompt[: len(after_rollover)] == after_rollover
    assert state.metrics["cache_prefix_epoch"] == 2


def test_append_layout_profiles_more_reusable_prefix_than_rebuild() -> None:
    rebuild_state = RunState.start("Inspect repository")
    append_state = RunState.start("Inspect repository")

    ContextBuilder().build_messages(rebuild_state, [])
    ContextBuilder(ContextConfig(prompt_layout="append")).build_messages(append_state, [])

    assert rebuild_state.metrics["stable_prefix_message_count"] == 1
    assert append_state.metrics["stable_prefix_message_count"] == 2
    assert (
        append_state.metrics["stable_prefix_estimated_tokens"]
        >= rebuild_state.metrics["stable_prefix_estimated_tokens"]
    )


def test_cache_namespace_is_first_dynamic_content_in_both_layouts() -> None:
    rebuild_state = RunState.start("Inspect", prompt_namespace="cache-experiment/round-1")
    append_state = RunState.start("Inspect", prompt_namespace="cache-experiment/round-1")

    rebuild = ContextBuilder(ContextConfig(prompt_layout="rebuild")).build_messages(
        rebuild_state,
        [],
    )
    append = ContextBuilder(ContextConfig(prompt_layout="append")).build_messages(
        append_state,
        [],
    )

    expected = "Prompt namespace: cache-experiment/round-1"
    assert rebuild[1]["content"].startswith(expected)
    assert append[1]["content"].startswith(expected)


def test_append_snapshot_freezes_budget_and_repetition_guidance() -> None:
    state = RunState.start("Finish the pending patch")
    state.metrics["max_turns"] = 10
    state.metrics["turns"] = 6
    state.metrics["repeated_file_reads"] = 2
    state.metrics["repeated_searches"] = 1
    snapshot = state_snapshot_text(state)
    trajectory = [_step("pytest -q", "verified", state_snapshot=snapshot)]

    messages = ContextBuilder(ContextConfig(prompt_layout="append")).build_messages(state, trajectory)

    assert "Converge now" in snapshot
    assert "I/O repetition guard" in snapshot
    assert "2 file read(s), 1 search(es)" in snapshot
    assert snapshot in messages[-1]["content"]


def test_action_economy_guidance_is_shared_by_rebuild_and_append_snapshots() -> None:
    state = RunState.start("Fix the failing implementation")
    state.constraints = [
        "Use these authoritative offline verification commands; do not install a different "
        "test runner: pytest -q"
    ]
    state.metrics["file_read_actions"] = 1

    rebuild_content = ContextBuilder().build_messages(state, [])[1]["content"]
    snapshot = state_snapshot_text(state)

    assert "Action economy:" in rebuild_content
    assert "the next bash action should apply that change" in rebuild_content
    assert "Do not inspect a test solely to reconfirm behavior" in rebuild_content
    assert "one authoritative verification command" in snapshot


def test_append_layout_formats_protocol_error_as_chat_turn() -> None:
    step = TrajectoryStep(
        action=None,
        observation=Observation(kind="protocol_error", message="invalid JSON"),
        state_snapshot="Budget status: retry safely.",
    )

    messages = ContextBuilder(ContextConfig(prompt_layout="append")).build_messages(
        RunState.start("Recover"),
        [step],
    )

    assert messages[-2] == {"role": "assistant", "content": "<protocol_error>"}
    assert messages[-1]["role"] == "user"
    assert "kind=protocol_error" in messages[-1]["content"]
    assert "Budget status: retry safely." in messages[-1]["content"]


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


def test_prompt_layout_does_not_change_compaction_state_or_retention() -> None:
    trajectory = [
        _step("pytest -q", "first failure", artifact_ids=["art_0001"]),
        _step("sed -n '1,120p' src/app.py", "inspected file"),
    ]
    rebuild_state = RunState.start("Debug failure")
    append_state = RunState.start("Debug failure")
    rebuild = ContextBuilder(
        ContextConfig(
            max_prompt_chars=10,
            recent_turns=1,
            summary_max_chars=2_000,
            retention_markers=("art_0001",),
            prompt_layout="rebuild",
        )
    )
    append = ContextBuilder(
        ContextConfig(
            max_prompt_chars=10,
            recent_turns=1,
            summary_max_chars=2_000,
            retention_markers=("art_0001",),
            prompt_layout="append",
        )
    )

    rebuild.maybe_compact(rebuild_state, trajectory)
    append.maybe_compact(append_state, trajectory)

    assert append_state.state_summary == rebuild_state.state_summary
    for name in (
        "context_compactions",
        "context_compacted_steps",
        "context_compaction_input_chars",
        "context_compaction_output_chars",
        "context_compaction_chars_saved",
        "context_retention_expected",
        "context_retention_retained",
        "context_retention_rate",
    ):
        assert append_state.metrics[name] == rebuild_state.metrics[name]


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
    trace = TraceRecorder()
    builder = ContextBuilder(
        skill_registry=SkillRegistry(tmp_path / "skills"),
        feedback_memory=FeedbackMemory(memory_path),
        trace=trace,
    )

    state = RunState.start("Use rg to inspect repository files")
    messages = builder.build_messages(state, [])
    builder.build_messages(state, [])

    assert "repo-inspection: Inspect repository structure." in messages[1]["content"]
    assert "caution: Use rg before broad file reads." in messages[1]["content"]
    assert state.metrics["guidance_skill_names"] == ["repo-inspection"]
    assert state.metrics["guidance_feedback_rule_ids"] == ["mem_1"]
    assert [event["event"] for event in trace.events].count("guidance_selected") == 1


def test_context_builder_adds_budget_pressure_after_thresholds() -> None:
    builder = ContextBuilder()
    state = RunState.start("Finish task")
    state.metrics["max_turns"] = 10
    state.metrics["turns"] = 6

    at_sixty = builder.build_messages(state, [])[1]["content"]
    state.metrics["turns"] = 8
    at_eighty = builder.build_messages(state, [])[1]["content"]
    state.metrics["turns"] = 9
    final_slot = builder.build_messages(state, [])[1]["content"]

    assert "Converge now" in at_sixty
    assert "Stop exploring" in at_eighty
    assert "final response slot" in final_slot
    assert "do not run another bash command" in final_slot


def test_context_builder_adds_io_repetition_guard() -> None:
    builder = ContextBuilder()
    state = RunState.start("Finish the pending patch")
    state.metrics["repeated_file_reads"] = 2
    state.metrics["repeated_searches"] = 1

    content = builder.build_messages(state, [])[1]["content"]

    assert "I/O repetition guard" in content
    assert "make the smallest required patch" in content


def _step(
    command: str,
    message: str,
    artifact_ids: list[str] | None = None,
    *,
    state_snapshot: str = "",
) -> TrajectoryStep:
    return TrajectoryStep(
        action=BashAction(command=command, purpose=message),
        observation=Observation(
            kind="command_result",
            exit_code=0,
            stdout_preview=message,
            artifact_ids=artifact_ids or [],
            message=message,
        ),
        state_snapshot=state_snapshot,
    )
