import json

from minicc.core.provider import ModelResponse, ModelUsage
from minicc.core.state import RunState
from minicc.memory.compaction import SemanticCompactor
from minicc.memory.feedback import FeedbackMemory


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


def test_semantic_compactor_requests_structured_summary_and_tracks_separate_usage() -> None:
    class Provider:
        def complete(self, messages, *, options=None):
            assert options.json_mode is True
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
