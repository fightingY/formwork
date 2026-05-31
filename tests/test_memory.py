import json

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
