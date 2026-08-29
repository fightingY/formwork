import pytest

from minicc.core.protocol import (
    TOOLS,
    AskAction,
    CodeModeAction,
    DelegateAction,
    FinalAction,
    MemoryReference,
    ProtocolError,
    SkillAction,
    ToolCall,
    action_to_dict,
    parse_tool_call,
)


def test_parse_bash_tool_call_defaults_timeout() -> None:
    action = parse_tool_call("call-1", "bash", {"command": "pytest -q", "description": "run tests"})

    assert isinstance(action, ToolCall)
    assert action.tool == "bash"
    assert action.arguments["command"] == "pytest -q"
    assert action.arguments["timeout_sec"] == 60
    assert action.arguments["description"] == "run tests"


def test_parse_read_edit_write_tool_calls() -> None:
    read = parse_tool_call("r1", "read", {"path": "src/app.py"})
    assert read == ToolCall(id="r1", tool="read", arguments={"path": "src/app.py"})

    edit = parse_tool_call(
        "e1",
        "edit",
        {"path": "a.py", "old_string": "x", "new_string": "y", "expected_hash": "sha256:abc"},
    )
    assert isinstance(edit, ToolCall)
    assert edit.tool == "edit"

    write = parse_tool_call("w1", "write", {"path": "a.py", "content": "hello"})
    assert isinstance(write, ToolCall)
    assert write.tool == "write"


def test_parse_ask_action() -> None:
    action = parse_tool_call("a1", "ask", {"question": "Allow network access?"})

    assert action == AskAction(question="Allow network access?")


def test_parse_skill_action() -> None:
    action = parse_tool_call("s1", "skill", {"name": "Python-Debugging"})

    assert action == SkillAction(name="python-debugging")


def test_parse_code_mode_action() -> None:
    action = parse_tool_call("c1", "code_mode", {"script": "print('hi')"})

    assert action == CodeModeAction(script="print('hi')")


def test_parse_delegate_action() -> None:
    action = parse_tool_call(
        "d1",
        "delegate",
        {"tasks": [{"id": "scout", "goal": "Inspect the parser", "provider": "fork"}]},
    )
    assert action == DelegateAction(
        tasks=({"id": "scout", "goal": "Inspect the parser", "provider": "fork"},),
    )


def test_parse_final_action() -> None:
    action = parse_tool_call("f1", "final", {"answer": "Done."})

    assert action == FinalAction(answer="Done.")


def test_parse_final_action_with_grounded_memory_references() -> None:
    action = parse_tool_call(
        "f2",
        "final",
        {
            "answer": "Done.",
            "memory": [{"path": "docs/contract.md", "line_start": 4, "line_end": 7}],
        },
    )

    assert action == FinalAction(
        answer="Done.",
        memory=(MemoryReference("docs/contract.md", 4, 7),),
    )
    assert action_to_dict(action)["memory"] == [
        {"path": "docs/contract.md", "line_start": 4, "line_end": 7}
    ]


@pytest.mark.parametrize(
    "memory",
    [
        [],
        [{"path": "../secret", "line_start": 1, "line_end": 1}],
        [{"path": "C:/secret", "line_start": 1, "line_end": 1}],
        [{"path": "docs/a.md", "line_start": 5, "line_end": 4}],
        [{"path": "docs/a.md", "line_start": 1, "line_end": 21}],
    ],
)
def test_parse_final_action_rejects_unsafe_memory(memory: list[object]) -> None:
    if memory:
        with pytest.raises(ProtocolError, match="final.memory"):
            parse_tool_call("f3", "final", {"answer": "Done.", "memory": memory})
    else:
        # An empty memory list is valid; this parametrize case exists only to
        # keep the "no memory" shape next to the unsafe-memory cases above.
        action = parse_tool_call("f3", "final", {"answer": "Done.", "memory": memory})
        assert action == FinalAction(answer="Done.")


def test_caps_timeout_to_loop_budget() -> None:
    action = parse_tool_call(
        "b1",
        "bash",
        {"command": "sleep 999", "timeout_sec": 999},
        max_timeout_sec=120,
    )

    assert isinstance(action, ToolCall)
    assert action.arguments["timeout_sec"] == 120


@pytest.mark.parametrize(
    "name,arguments,error",
    [
        ("bash", {"command": ""}, "bash.command"),
        ("bash", {"command": "echo ok", "timeout_sec": 0}, "bash.timeout_sec"),
        ("bash", {"command": "echo ok", "description": 42}, "bash.description"),
        ("ask", {"question": ""}, "ask.question"),
        ("skill", {"name": "../secret"}, "skill.name"),
        ("final", {"answer": ""}, "final.answer"),
        ("unknown", {}, "Unknown tool"),
    ],
)
def test_parse_tool_call_rejects_invalid_parameters(
    name: str, arguments: dict[str, object], error: str
) -> None:
    with pytest.raises(ProtocolError, match=error):
        parse_tool_call("x1", name, arguments)


def test_tools_schema_covers_all_known_tool_names() -> None:
    tool_names = {tool["function"]["name"] for tool in TOOLS}
    assert tool_names == {"read", "edit", "write", "bash", "code_mode", "ask", "skill", "final", "delegate"}
    for tool in TOOLS:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["additionalProperties"] is False
