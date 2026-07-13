from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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
class FinalAction:
    answer: str
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
    """Accept common provider wrappers while preserving strict JSON validation."""
    value = text.strip()
    if value.startswith("<function>") and value.endswith("</function>"):
        value = value[len("<function>") : -len("</function>")].strip()
    if value.startswith("```") and value.endswith("```"):
        first_newline = value.find("\n")
        if first_newline > 0:
            value = value[first_newline + 1 : -3].strip()
    return value


def action_to_dict(action: Action) -> dict[str, Any]:
    data = asdict(action)
    action_type = data.pop("type")
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
    return FinalAction(answer=answer.strip())


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
