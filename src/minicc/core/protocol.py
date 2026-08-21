from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

ActionType = Literal["bash", "skill", "ask", "final", "tool_calls"]


@dataclass(frozen=True)
class BashAction:
    command: str
    timeout_sec: int = 60
    purpose: str = ""
    type: Literal["bash"] = "bash"


@dataclass(frozen=True)
class ToolCall:
    """A structured capability invocation in a model response."""

    id: str
    tool: Literal["read", "edit", "write", "bash"]
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ToolCallsAction:
    calls: tuple[ToolCall, ...]
    type: Literal["tool_calls"] = "tool_calls"


@dataclass(frozen=True)
class AskAction:
    question: str
    type: Literal["ask"] = "ask"


@dataclass(frozen=True)
class SkillAction:
    name: str
    type: Literal["skill"] = "skill"


@dataclass(frozen=True)
class MemoryReference:
    path: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class FinalAction:
    answer: str
    memory: tuple[MemoryReference, ...] = ()
    type: Literal["final"] = "final"


Action = BashAction | SkillAction | AskAction | FinalAction | ToolCallsAction


class ProtocolError(ValueError):
    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.raw_text = raw_text


def parse_action(
    text: str,
    *,
    default_timeout_sec: int = 60,
    max_timeout_sec: int | None = None,
    max_tool_calls: int = 16,
) -> Action:
    text = _unwrap_model_json(text)
    try:
        payload = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ProtocolError(
            f"Model output must be exactly one JSON object: {exc.msg}",
            raw_text=text,
        ) from exc

    if not isinstance(payload, dict):
        raise ProtocolError("Action payload must be a JSON object.", raw_text=text)

    action_type = payload.get("type")
    if action_type != "tool_calls" and "calls" in payload:
        raise ProtocolError(
            "Control and legacy actions cannot be mixed with tool_calls.", raw_text=text
        )
    if action_type == "tool_calls":
        return _parse_tool_calls(payload, text, default_timeout_sec, max_timeout_sec, max_tool_calls)
    if action_type == "bash":
        return _parse_bash(payload, text, default_timeout_sec, max_timeout_sec)
    if action_type == "skill":
        return _parse_skill(payload, text)
    if action_type == "ask":
        return _parse_ask(payload, text)
    if action_type == "final":
        return _parse_final(payload, text)

    raise ProtocolError(
        "Action type must be one of: bash, skill, ask, final, tool_calls.",
        raw_text=text,
    )


def _unwrap_model_json(text: str) -> str:
    """Extract exactly one top-level JSON object from optional provider text."""
    value = text.strip()
    decoder = json.JSONDecoder()
    objects: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    while cursor < len(value):
        start = value.find("{", cursor)
        if start < 0:
            break
        try:
            payload, end = decoder.raw_decode(value, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(payload, dict):
            objects.append((start, end, payload))
            cursor = end
        else:
            cursor = start + 1

    if len(objects) == 1:
        start, end, _ = objects[0]
        return value[start:end]
    return value


def action_to_dict(action: Action) -> dict[str, Any]:
    if isinstance(action, ToolCallsAction):
        return {
            "type": "tool_calls",
            "calls": [
                {"id": call.id, "tool": call.tool, "arguments": dict(call.arguments)}
                for call in action.calls
            ],
        }
    data = asdict(action)
    action_type = data.pop("type")
    if action_type == "final" and not data.get("memory"):
        data.pop("memory", None)
    elif action_type == "final":
        data["memory"] = list(data["memory"])
    return {"type": action_type, **data}


def _parse_tool_calls(
    payload: dict[str, Any],
    raw_text: str,
    default_timeout_sec: int,
    max_timeout_sec: int | None,
    max_tool_calls: int,
) -> ToolCallsAction:
    calls = payload.get("calls")
    if set(payload) != {"type", "calls"}:
        raise ProtocolError(
            "tool_calls cannot be mixed with control-action fields.", raw_text=raw_text
        )
    if not isinstance(calls, list) or not calls:
        raise ProtocolError("tool_calls.calls must be a non-empty array.", raw_text=raw_text)
    if max_tool_calls <= 0:
        raise ValueError("max_tool_calls must be a positive integer")
    if len(calls) > max_tool_calls:
        raise ProtocolError(
            f"tool_calls.calls supports at most {max_tool_calls} calls.", raw_text=raw_text
        )
    parsed: list[ToolCall] = []
    seen: set[str] = set()
    for item in calls:
        if not isinstance(item, dict):
            raise ProtocolError("Each tool call must be an object.", raw_text=raw_text)
        call_id = item.get("id")
        tool = item.get("tool")
        arguments = item.get("arguments")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ProtocolError("tool_calls call id must be a non-empty string.", raw_text=raw_text)
        call_id = call_id.strip()
        if call_id in seen:
            raise ProtocolError(f"Duplicate tool call id: {call_id}.", raw_text=raw_text)
        seen.add(call_id)
        if tool not in {"read", "edit", "write", "bash"}:
            raise ProtocolError(f"Unknown tool: {tool!r}.", raw_text=raw_text)
        if not isinstance(arguments, dict):
            raise ProtocolError(f"{tool}.arguments must be a JSON object.", raw_text=raw_text)
        normalized = dict(arguments)
        _validate_tool_arguments(tool, normalized, raw_text)
        if tool == "bash":
            command = normalized.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ProtocolError("bash.command must be a non-empty string.", raw_text=raw_text)
            timeout = _parse_timeout(normalized.get("timeout_sec"), default_timeout_sec, raw_text)
            if max_timeout_sec is not None:
                timeout = min(timeout, max_timeout_sec)
            normalized["command"] = command.strip()
            normalized["timeout_sec"] = timeout
            for key in ("description", "purpose", "workdir"):
                if key in normalized and not isinstance(normalized[key], str):
                    raise ProtocolError(f"bash.{key} must be a string when provided.", raw_text=raw_text)
        parsed.append(ToolCall(id=call_id, tool=tool, arguments=normalized))
    return ToolCallsAction(calls=tuple(parsed))


def _validate_tool_arguments(tool: str, arguments: dict[str, Any], raw_text: str) -> None:
    allowed = {
        "read": {"path", "offset", "limit"},
        "edit": {"path", "old_string", "new_string", "replace_all", "expected_hash"},
        "write": {"path", "content", "expected_hash"},
        "bash": {"command", "description", "purpose", "timeout_sec", "workdir"},
    }[tool]
    unexpected = set(arguments) - allowed
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ProtocolError(f"{tool}.arguments contains unknown field(s): {names}.", raw_text=raw_text)
    path = arguments.get("path")
    if tool in {"read", "edit", "write"} and (not isinstance(path, str) or not path.strip()):
        raise ProtocolError(f"{tool}.path must be a non-empty string.", raw_text=raw_text)
    if tool == "read":
        for name in ("offset", "limit"):
            if name in arguments:
                if isinstance(arguments[name], bool) or not isinstance(arguments[name], int) or arguments[name] <= 0:
                    raise ProtocolError(
                        f"read.{name} must be a positive integer.", raw_text=raw_text
                    )
    elif tool == "edit":
        if not isinstance(arguments.get("old_string"), str) or not arguments["old_string"]:
            raise ProtocolError("edit.old_string must be a non-empty string.", raw_text=raw_text)
        if not isinstance(arguments.get("new_string"), str):
            raise ProtocolError("edit.new_string must be a string.", raw_text=raw_text)
        if not isinstance(arguments.get("expected_hash"), str) or not arguments["expected_hash"]:
            raise ProtocolError("edit.expected_hash must be a non-empty string.", raw_text=raw_text)
        if "replace_all" in arguments and not isinstance(arguments["replace_all"], bool):
            raise ProtocolError("edit.replace_all must be a boolean.", raw_text=raw_text)
    elif tool == "write":
        if not isinstance(arguments.get("content"), str):
            raise ProtocolError("write.content must be a string.", raw_text=raw_text)
        if "expected_hash" in arguments and not isinstance(arguments["expected_hash"], str):
            raise ProtocolError("write.expected_hash must be a string.", raw_text=raw_text)


def action_to_json(action: Action) -> str:
    return json.dumps(action_to_dict(action), ensure_ascii=False)


def _parse_bash(
    payload: dict[str, Any],
    raw_text: str,
    default_timeout_sec: int,
    max_timeout_sec: int | None,
) -> BashAction:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ProtocolError("bash.command must be a non-empty string.", raw_text=raw_text)

    timeout_sec = _parse_timeout(payload.get("timeout_sec"), default_timeout_sec, raw_text)
    if max_timeout_sec is not None:
        timeout_sec = min(timeout_sec, max_timeout_sec)

    purpose = payload.get("purpose", "")
    if purpose is None:
        purpose = ""
    if not isinstance(purpose, str):
        raise ProtocolError("bash.purpose must be a string when provided.", raw_text=raw_text)

    return BashAction(command=command.strip(), timeout_sec=timeout_sec, purpose=purpose.strip())


def _parse_ask(payload: dict[str, Any], raw_text: str) -> AskAction:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ProtocolError("ask.question must be a non-empty string.", raw_text=raw_text)
    return AskAction(question=question.strip())


def _parse_skill(payload: dict[str, Any], raw_text: str) -> SkillAction:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProtocolError("skill.name must be a non-empty string.", raw_text=raw_text)
    normalized = name.strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", normalized) is None:
        raise ProtocolError("skill.name is not a valid catalog name.", raw_text=raw_text)
    return SkillAction(name=normalized)


def _parse_final(payload: dict[str, Any], raw_text: str) -> FinalAction:
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ProtocolError("final.answer must be a non-empty string.", raw_text=raw_text)
    raw_memory = payload.get("memory", [])
    if not isinstance(raw_memory, list):
        raise ProtocolError("final.memory must be a list when provided.", raw_text=raw_text)
    if len(raw_memory) > 8:
        raise ProtocolError("final.memory supports at most 8 references.", raw_text=raw_text)
    memory = tuple(_parse_memory_reference(item, raw_text) for item in raw_memory)
    return FinalAction(answer=answer.strip(), memory=memory)


def _parse_memory_reference(value: Any, raw_text: str) -> MemoryReference:
    if not isinstance(value, dict):
        raise ProtocolError("final.memory entries must be objects.", raw_text=raw_text)
    path = str(value.get("path") or "").replace("\\", "/").strip().strip("/")
    parts = PurePosixPath(path).parts
    if (
        not path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
        or ".." in parts
        or "." in parts
    ):
        raise ProtocolError("final.memory.path must be a safe relative path.", raw_text=raw_text)
    line_start = _positive_int(value.get("line_start"), "final.memory.line_start", raw_text)
    line_end = _positive_int(value.get("line_end"), "final.memory.line_end", raw_text)
    if line_end < line_start:
        raise ProtocolError("final.memory.line_end must not precede line_start.", raw_text=raw_text)
    if line_end - line_start + 1 > 20:
        raise ProtocolError("final.memory references may span at most 20 lines.", raw_text=raw_text)
    return MemoryReference(path=path, line_start=line_start, line_end=line_end)


def _positive_int(value: Any, name: str, raw_text: str) -> int:
    if isinstance(value, bool):
        raise ProtocolError(f"{name} must be a positive integer.", raw_text=raw_text)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} must be a positive integer.", raw_text=raw_text) from exc
    if parsed <= 0:
        raise ProtocolError(f"{name} must be a positive integer.", raw_text=raw_text)
    return parsed


def _parse_timeout(value: Any, default_timeout_sec: int, raw_text: str) -> int:
    if value is None:
        return default_timeout_sec
    if isinstance(value, bool):
        raise ProtocolError("bash.timeout_sec must be a positive integer.", raw_text=raw_text)
    try:
        timeout_sec = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("bash.timeout_sec must be a positive integer.", raw_text=raw_text) from exc
    if timeout_sec <= 0:
        raise ProtocolError("bash.timeout_sec must be a positive integer.", raw_text=raw_text)
    return timeout_sec
