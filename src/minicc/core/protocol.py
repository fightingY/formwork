from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Literal, Union


ActionType = Literal["bash", "ask", "final"]


@dataclass(frozen=True)
class BashAction:
    command: str
    timeout_sec: int = 60
    purpose: str = ""
    type: Literal["bash"] = "bash"


@dataclass(frozen=True)
class AskAction:
    question: str
    type: Literal["ask"] = "ask"


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


Action = Union[BashAction, AskAction, FinalAction]


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
    if action_type == "bash":
        return _parse_bash(payload, text, default_timeout_sec, max_timeout_sec)
    if action_type == "ask":
        return _parse_ask(payload, text)
    if action_type == "final":
        return _parse_final(payload, text)

    raise ProtocolError(
        "Action type must be one of: bash, ask, final.",
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
    data = asdict(action)
    action_type = data.pop("type")
    if action_type == "final" and not data.get("memory"):
        data.pop("memory", None)
    elif action_type == "final":
        data["memory"] = list(data["memory"])
    return {"type": action_type, **data}


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
