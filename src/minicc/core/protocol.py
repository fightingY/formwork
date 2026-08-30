from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, cast

# Protocol schema version. Bumped from the old LEDGER_SCHEMA_VERSION=2 lineage to
# mark the non-backward-compatible move from a text-JSON action protocol to
# provider-native tool calling: old runs/traces/checkpoints are not replayable
# under this version. core/ledger.py, core/session_store.py, trace/recorder.py,
# and trace/replay.py all import this single constant instead of keeping their
# own separate version numbers.
PROTOCOL_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class BashAction:
    command: str
    timeout_sec: int = 60
    purpose: str = ""
    type: Literal["bash"] = "bash"
    progress: str = ""


@dataclass(frozen=True)
class ToolCall:
    """A dispatchable capability invocation adapted from a native tool call.

    Covers ``read``/``edit``/``write``/``bash`` — the tools that go through
    :class:`~minicc.core.tooling.ToolCallScheduler`. ``bash`` calls are converted
    into an internal :class:`BashAction` by the runner right before they reach
    the policy chain / sandbox executor, which stay ``BashAction``-shaped.
    """

    id: str
    tool: Literal["read", "edit", "write", "bash", "delegate"]
    arguments: Mapping[str, Any]


class DelegateToolCall(ToolCall):
    """Concurrency-safe delegate tool call.

    Kept as a ToolCall subclass so old code comparing parsed delegates with
    DelegateAction values remains source-compatible during the protocol move.
    """

    def __init__(self, *, id: str, arguments: Mapping[str, Any]) -> None:
        super().__init__(id=id, tool="delegate", arguments=arguments)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DelegateAction):
            return (
                tuple(self.arguments.get("tasks", ())) == other.tasks
                and str(self.arguments.get("join", "all")) == other.join
                and bool(self.arguments.get("background", False)) == other.background
            )
        return super().__eq__(other)


@dataclass(frozen=True)
class AskAction:
    question: str
    type: Literal["ask"] = "ask"
    progress: str = ""


@dataclass(frozen=True)
class SkillAction:
    name: str
    type: Literal["skill"] = "skill"
    progress: str = ""


@dataclass(frozen=True)
class CodeModeAction:
    """Batch script invocation: the model calls read/edit/write/bash programmatically
    via the injected facade instead of one tool call per turn."""

    script: str
    type: Literal["code_mode"] = "code_mode"
    progress: str = ""


@dataclass(frozen=True)
class DelegateAction:
    """Delegate one or more bounded tasks to isolated child agents."""

    tasks: tuple[dict[str, Any], ...]
    join: Literal["all", "any"] = "all"
    background: bool = False
    type: Literal["delegate"] = "delegate"
    progress: str = ""


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
    progress: str = ""


@dataclass(frozen=True)
class ToolCallBatch:
    """Trajectory-recording container for a turn's dispatched ToolCall set.

    Not a model-facing action type — the model's native response is already a
    plain array of tool_calls; this only exists so ``TrajectoryStep.action``
    (a single field) can record "the model issued N calls in one turn" as one
    step, with one aggregated observation, the same way the old (now removed)
    ``ToolCallsAction`` did for the hybrid-v3.6 profile.
    """

    calls: tuple[ToolCall, ...]
    type: Literal["tool_call_batch"] = "tool_call_batch"


Action = BashAction | ToolCall | ToolCallBatch | SkillAction | AskAction | FinalAction | CodeModeAction | DelegateAction

CONTROL_TOOL_NAMES = frozenset({"final", "ask", "skill", "code_mode", "delegate"})
KNOWN_TOOL_NAMES = frozenset({"read", "edit", "write", "bash", *CONTROL_TOOL_NAMES})


class ProtocolError(ValueError):
    """Raised when an already-parsed native tool call carries invalid arguments.

    Unlike the old text-JSON protocol, there is no "malformed top-level object"
    case here — the provider API guarantees ``tool_calls`` structure. This error
    only covers per-tool argument validation (e.g. missing ``expected_hash``).
    """

    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.raw_text = raw_text


def parse_tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    default_timeout_sec: int = 60,
    max_timeout_sec: int | None = None,
) -> Action:
    """Adapt one native ``id`` + ``function.name`` + parsed ``function.arguments`` into an Action.

    ``arguments`` is already a dict (the caller has done ``json.loads`` on the
    provider's raw ``function.arguments`` string). This function only validates
    per-tool argument shape — the outer "is this valid JSON" concern belongs to
    the caller, since a JSON-decode failure here is a provider-contract violation,
    not a recoverable protocol error.

    ``read``/``edit``/``write``/``bash``/``delegate`` become scheduler-ready :class:`ToolCall`
    (``bash``'s arguments are normalized — trimmed command, clamped timeout_sec —
    but the actual :class:`BashAction` construction happens in ``core.tooling``
    right before the policy chain, same as before this refactor). The control
    tools (``final``/``ask``/``skill``/``code_mode``) become their own Action type;
    delegate is a concurrency-safe tool call.
    """
    if name not in KNOWN_TOOL_NAMES:
        raise ProtocolError(f"Unknown tool: {name!r}.")
    normalized = dict(arguments)
    _validate_tool_arguments(name, normalized)
    if name == "read":
        return ToolCall(id=call_id, tool="read", arguments=normalized)
    if name == "edit":
        return ToolCall(id=call_id, tool="edit", arguments=normalized)
    if name == "write":
        return ToolCall(id=call_id, tool="write", arguments=normalized)
    if name == "bash":
        return ToolCall(id=call_id, tool="bash", arguments=_normalize_bash_arguments(normalized, default_timeout_sec, max_timeout_sec))
    if name == "ask":
        return _build_ask(normalized)
    if name == "skill":
        return _build_skill(normalized)
    if name == "code_mode":
        return _build_code_mode(normalized)
    if name == "delegate":
        return DelegateToolCall(id=call_id, arguments=normalized)
    return _build_final(normalized)


def action_to_dict(action: Action) -> dict[str, Any]:
    if isinstance(action, ToolCall):
        return {"type": action.tool, "id": action.id, "arguments": dict(action.arguments)}
    if isinstance(action, ToolCallBatch):
        return {
            "type": "tool_call_batch",
            "calls": [
                {"id": call.id, "tool": call.tool, "arguments": dict(call.arguments)}
                for call in action.calls
            ],
        }
    data = asdict(action)
    action_type = data.pop("type")
    if not data.get("progress"):
        data.pop("progress", None)
    if action_type == "final" and not data.get("memory"):
        data.pop("memory", None)
    elif action_type == "final":
        data["memory"] = list(data["memory"])
    return {"type": action_type, **data}


def action_to_json(action: Action) -> str:
    import json

    return json.dumps(action_to_dict(action), ensure_ascii=False)


def action_from_dict(data: dict[str, Any]) -> Action:
    """Reconstruct an Action from ``action_to_dict``'s output (checkpoint/trajectory restore).

    Trusts the persisted shape (it was valid when written) rather than
    re-running full argument validation.
    """
    action_type = data.get("type")
    if action_type == "read":
        return ToolCall(id=str(data.get("id", "")), tool="read", arguments=dict(data.get("arguments", {})))
    if action_type == "edit":
        return ToolCall(id=str(data.get("id", "")), tool="edit", arguments=dict(data.get("arguments", {})))
    if action_type == "write":
        return ToolCall(id=str(data.get("id", "")), tool="write", arguments=dict(data.get("arguments", {})))
    if action_type == "bash":
        return ToolCall(id=str(data.get("id", "")), tool="bash", arguments=dict(data.get("arguments", {})))
    if action_type == "tool_call_batch":
        return ToolCallBatch(
            calls=tuple(
                ToolCall(
                    id=str(item.get("id", "")),
                    tool=_fs_or_bash_tool_literal(item.get("tool")),
                    arguments=dict(item.get("arguments", {})),
                )
                for item in data.get("calls", [])
                if isinstance(item, dict)
            )
        )
    if action_type == "ask":
        return AskAction(question=str(data.get("question", "")), progress=str(data.get("progress", "")))
    if action_type == "skill":
        return SkillAction(name=str(data.get("name", "")), progress=str(data.get("progress", "")))
    if action_type == "code_mode":
        return CodeModeAction(script=str(data.get("script", "")), progress=str(data.get("progress", "")))
    if action_type == "delegate":
        if "id" in data and "arguments" in data:
            return DelegateToolCall(id=str(data.get("id", "")), arguments=dict(data.get("arguments", {})))
        return DelegateAction(
            tasks=tuple(dict(item) for item in data.get("tasks", []) if isinstance(item, dict)),
            join=cast(Literal["all", "any"], str(data.get("join", "all"))),
            background=bool(data.get("background", False)),
            progress=str(data.get("progress", "")),
        )
    if action_type == "final":
        memory = tuple(
            MemoryReference(
                path=str(item.get("path", "")),
                line_start=int(item.get("line_start", 1)),
                line_end=int(item.get("line_end", 1)),
            )
            for item in data.get("memory", [])
            if isinstance(item, dict)
        )
        return FinalAction(answer=str(data.get("answer", "")), memory=memory, progress=str(data.get("progress", "")))
    raise ProtocolError(f"Unknown persisted action type: {action_type!r}.")


def _fs_or_bash_tool_literal(value: Any) -> Literal["read", "edit", "write", "bash", "delegate"]:
    if value in {"read", "edit", "write", "bash", "delegate"}:
        return value
    raise ProtocolError(f"Unknown persisted tool_call_batch member tool: {value!r}.")


def _validate_tool_arguments(tool: str, arguments: dict[str, Any]) -> None:
    allowed = {
        "read": {"path", "offset", "limit"},
        "edit": {"path", "old_string", "new_string", "replace_all", "expected_hash"},
        "write": {"path", "content", "expected_hash"},
        "bash": {"command", "description", "purpose", "timeout_sec", "workdir"},
        "ask": {"question"},
        "skill": {"name"},
        "code_mode": {"script"},
        "delegate": {"tasks", "join", "background"},
        "final": {"answer", "memory"},
    }[tool]
    unexpected = set(arguments) - allowed
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ProtocolError(f"{tool}.arguments contains unknown field(s): {names}.")
    path = arguments.get("path")
    if tool in {"read", "edit", "write"} and (not isinstance(path, str) or not path.strip()):
        raise ProtocolError(f"{tool}.path must be a non-empty string.")
    if tool == "read":
        for name in ("offset", "limit"):
            if name in arguments:
                if isinstance(arguments[name], bool) or not isinstance(arguments[name], int) or arguments[name] <= 0:
                    raise ProtocolError(f"read.{name} must be a positive integer.")
    elif tool == "edit":
        if not isinstance(arguments.get("old_string"), str) or not arguments["old_string"]:
            raise ProtocolError("edit.old_string must be a non-empty string.")
        if not isinstance(arguments.get("new_string"), str):
            raise ProtocolError("edit.new_string must be a string.")
        if not isinstance(arguments.get("expected_hash"), str) or not arguments["expected_hash"]:
            raise ProtocolError("edit.expected_hash must be a non-empty string.")
        if "replace_all" in arguments and not isinstance(arguments["replace_all"], bool):
            raise ProtocolError("edit.replace_all must be a boolean.")
    elif tool == "write":
        if not isinstance(arguments.get("content"), str):
            raise ProtocolError("write.content must be a string.")
        if "expected_hash" in arguments and not isinstance(arguments["expected_hash"], str):
            raise ProtocolError("write.expected_hash must be a string.")


def _normalize_bash_arguments(
    payload: dict[str, Any],
    default_timeout_sec: int,
    max_timeout_sec: int | None,
) -> dict[str, Any]:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ProtocolError("bash.command must be a non-empty string.")
    timeout_sec = _parse_timeout(payload.get("timeout_sec"), default_timeout_sec)
    if max_timeout_sec is not None:
        timeout_sec = min(timeout_sec, max_timeout_sec)
    purpose = payload.get("description", payload.get("purpose", ""))
    if purpose is None:
        purpose = ""
    if not isinstance(purpose, str):
        raise ProtocolError("bash.description must be a string when provided.")
    payload["command"] = command.strip()
    payload["timeout_sec"] = timeout_sec
    payload["description"] = purpose.strip()
    payload.pop("purpose", None)
    return payload


TOOLS: tuple[dict[str, Any], ...] = (
    {"type": "function", "function": {
        "name": "read", "description": "Read a bounded slice of a workspace-relative file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1},
        }, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "edit",
        "description": "Replace old_string with new_string in an existing file; requires expected_hash for optimistic locking.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_string": {"type": "string"},
            "new_string": {"type": "string"}, "replace_all": {"type": "boolean"},
            "expected_hash": {"type": "string"},
        }, "required": ["path", "old_string", "new_string", "expected_hash"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "write",
        "description": "Write full file content; requires expected_hash when overwriting an existing file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}, "expected_hash": {"type": "string"},
        }, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "bash", "description": "Run a shell command inside the sandbox.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout_sec": {"type": "integer", "minimum": 1},
            "description": {"type": "string"},
        }, "required": ["command"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "code_mode",
        "description": (
            "Run a Python script inside the same sandbox that calls read/edit/write/bash "
            "programmatically via the injected minicc_tools facade, for batch multi-step operations."
        ),
        "parameters": {"type": "object", "properties": {
            "script": {"type": "string"},
        }, "required": ["script"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "delegate",
        "description": "Run isolated child agents and return their structured summaries and facts.",
        "parameters": {"type": "object", "properties": {
            "tasks": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "goal": {"type": "string"}, "role": {"type": "string"},
                "capability_profile": {"type": "string"}, "provider": {"type": "string", "enum": ["spawn", "fork"]},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "max_turns": {"type": "integer", "minimum": 0}, "timeout_sec": {"type": "number", "minimum": 0},
                "output_schema": {"type": "string"},
            }, "required": ["id", "goal"], "additionalProperties": False}},
            "join": {"type": "string", "enum": ["all", "any"]}, "background": {"type": "boolean"},
        }, "required": ["tasks"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "ask", "description": "Ask the user a concrete question when blocked by missing input.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
        }, "required": ["question"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "skill", "description": "Load one skill's instructions from the frozen run catalog.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
        }, "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "final",
        "description": "Finish the task with a final answer and optional grounding memory references.",
        "parameters": {"type": "object", "properties": {
            "answer": {"type": "string"},
            "memory": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "line_start": {"type": "integer"}, "line_end": {"type": "integer"},
            }, "required": ["path", "line_start", "line_end"]}},
        }, "required": ["answer"], "additionalProperties": False}}},
)


def bash_action_from_tool_call(call: ToolCall) -> BashAction:
    """Build the internal :class:`BashAction` a ``bash`` ToolCall carries.

    Kept separate from parsing so ``core.tooling`` can construct it right before
    handing off to the policy chain / sandbox executor, exactly like the pre-refactor
    dispatch boundary.
    """
    args = call.arguments
    return BashAction(
        command=str(args.get("command", "")),
        timeout_sec=int(args.get("timeout_sec", 60)),
        purpose=str(args.get("description", "")),
    )


def _build_ask(payload: dict[str, Any]) -> AskAction:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ProtocolError("ask.question must be a non-empty string.")
    return AskAction(question=question.strip())


def _build_skill(payload: dict[str, Any]) -> SkillAction:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProtocolError("skill.name must be a non-empty string.")
    normalized = name.strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", normalized) is None:
        raise ProtocolError("skill.name is not a valid catalog name.")
    return SkillAction(name=normalized)


def _build_code_mode(payload: dict[str, Any]) -> CodeModeAction:
    script = payload.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ProtocolError("code_mode.script must be a non-empty string.")
    return CodeModeAction(script=script)


def _build_delegate(payload: dict[str, Any]) -> DelegateAction:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ProtocolError("delegate.tasks must be a non-empty list.")
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise ProtocolError(f"delegate.tasks[{index}] must be an object.")
        task = dict(raw)
        task_id = task.get("id")
        goal = task.get("goal")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProtocolError(f"delegate.tasks[{index}].id must be a non-empty string.")
        if not isinstance(goal, str) or not goal.strip():
            raise ProtocolError(f"delegate.tasks[{index}].goal must be a non-empty string.")
        task["id"] = task_id.strip()
        task["goal"] = goal.strip()
        tasks.append(task)
    join = payload.get("join", "all")
    if join not in {"all", "any"}:
        raise ProtocolError("delegate.join must be 'all' or 'any'.")
    return DelegateAction(tasks=tuple(tasks), join=join, background=bool(payload.get("background", False)))


def _build_final(payload: dict[str, Any]) -> FinalAction:
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ProtocolError("final.answer must be a non-empty string.")
    raw_memory = payload.get("memory", [])
    if not isinstance(raw_memory, list):
        raise ProtocolError("final.memory must be a list when provided.")
    if len(raw_memory) > 8:
        raise ProtocolError("final.memory supports at most 8 references.")
    memory = tuple(_parse_memory_reference(item) for item in raw_memory)
    return FinalAction(answer=answer.strip(), memory=memory)


def _parse_memory_reference(value: Any) -> MemoryReference:
    if not isinstance(value, dict):
        raise ProtocolError("final.memory entries must be objects.")
    path = str(value.get("path") or "").replace("\\", "/").strip().strip("/")
    parts = PurePosixPath(path).parts
    if (
        not path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
        or ".." in parts
        or "." in parts
    ):
        raise ProtocolError("final.memory.path must be a safe relative path.")
    line_start = _positive_int(value.get("line_start"), "final.memory.line_start")
    line_end = _positive_int(value.get("line_end"), "final.memory.line_end")
    if line_end < line_start:
        raise ProtocolError("final.memory.line_end must not precede line_start.")
    if line_end - line_start + 1 > 20:
        raise ProtocolError("final.memory references may span at most 20 lines.")
    return MemoryReference(path=path, line_start=line_start, line_end=line_end)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ProtocolError(f"{name} must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ProtocolError(f"{name} must be a positive integer.")
    return parsed


def _parse_timeout(value: Any, default_timeout_sec: int) -> int:
    if value is None:
        return default_timeout_sec
    if isinstance(value, bool):
        raise ProtocolError("bash.timeout_sec must be a positive integer.")
    try:
        timeout_sec = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("bash.timeout_sec must be a positive integer.") from exc
    if timeout_sec <= 0:
        raise ProtocolError("bash.timeout_sec must be a positive integer.")
    return timeout_sec
