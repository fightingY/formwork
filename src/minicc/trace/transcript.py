"""Stable, compact and redacted human-readable projection of trace events."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from minicc.core.events import EventLog
from minicc.core.projections import ProjectionRegistry, TranscriptProjection

SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)=([^\s&]+)")
PROJECTED_EVENTS = {
    "run_started",
    "run_completed",
    "run_failed",
    "run_interrupted",
    "action_started",
    "tool/call",
    "tool/result",
    "observation_created",
    "policy_decision",
    "child_start",
    "child_result",
    "workflow_summary_observation",
}
MAX_PREVIEW_CHARS = 1_200
MAX_COMMAND_CHARS = 800


def project_event_log(log: EventLog) -> list[dict[str, Any]]:
    """Return the durable transcript projection from the new event log.

    This is intentionally separate from the retired trace projector below;
    telemetry loss cannot affect the returned conversation records.
    """
    registry = ProjectionRegistry()
    registry.register(TranscriptProjection())
    registry.fold(log.session_id or "", log.events)
    return registry.value(log.session_id or "", "transcript").get("events", [])


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET.sub(r"\1=[REDACTED]", value)
    if isinstance(value, dict):
        secret_keys = {"authorization", "api_key", "token", "password", "secret"}
        return {
            str(key): redact(item)
            for key, item in value.items()
            if str(key).lower() not in secret_keys
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class TranscriptProjector:
    def __init__(self, *, root_run_id: str = "") -> None:
        self.root_run_id = root_run_id
        self.records: list[dict[str, Any]] = []
        self._turn = 0
        self._event_seq = 0

    def project_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Project one already-selected trace event into the durable transcript schema."""
        self._event_seq += 1
        event_name = str(event.get("event", "status"))
        if event_name == "action_started" and not self._turn:
            self._turn = 1
        action = event.get("action") if isinstance(event.get("action"), dict) else None
        intent, intent_kind = _intent(action, event_name, event)
        kind = _kind(event_name)
        links = list(event.get("links", []))
        links.append(f"trace://trace.jsonl#event={self._event_seq}")
        record = {
            "schema_version": 1,
            "root_run_id": self.root_run_id or event.get("run_id", ""),
            "run_id": event.get("run_id", ""),
            "parent_run_id": event.get("parent_run_id"),
            "workflow_id": event.get("workflow_id"),
            "task_id": event.get("task_id"),
            "role": event.get("role"),
            "turn_id": event.get("turn_id")
            or (f"turn-{self._turn}" if self._turn and kind != "status" else None),
            "step_id": event.get("step_id"),
            "span_id": event.get("span_id"),
            "parent_span_id": event.get("parent_span_id"),
            "kind": kind,
            "event": event_name,
            "timestamp": event.get("created_at"),
            "intent": intent,
            "intent_kind": intent_kind,
            "action": _action_summary(action, event),
            "observation": _observation_summary(event),
            "links": links,
        }
        self.records.append(record)
        return record

    def project(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for event in events:
            event_name = str(event.get("event", ""))
            if event_name == "prompt_built":
                self._turn += 1
                continue
            if event_name not in PROJECTED_EVENTS:
                continue
            if event_name == "policy_decision" and event.get("decision_type") == "allow":
                continue
            projected.append(self.project_event(event))
        return projected

    def write(self, run_dir: Path) -> tuple[Path, Path]:
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "transcript.jsonl"
        md_path = run_dir / "transcript.md"
        json_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in self.records),
            encoding="utf-8",
        )
        md_path.write_text(_format_markdown(self.records), encoding="utf-8")
        return json_path, md_path


def project_trace(trace_path: Path, run_dir: Path | None = None) -> tuple[Path, Path]:
    events: list[dict[str, Any]] = []
    if trace_path.exists():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    target = run_dir or trace_path.parent
    projector = TranscriptProjector()
    projector.project(events)
    return projector.write(target)


def _kind(event_name: str) -> str:
    if event_name in {"action_started", "tool/call"}:
        return "action"
    if event_name in {"tool/result", "observation_created"}:
        return "observation"
    if event_name == "child_start":
        return "child_start"
    if event_name == "child_result":
        return "child_end"
    if event_name == "workflow_summary_observation":
        return "summary"
    if event_name == "policy_decision":
        return "policy"
    return "status"


def _intent(
    action: dict[str, Any] | None, event_name: str, event: dict[str, Any]
) -> tuple[str, str | None]:
    if action:
        explicit = action.get("intent") or action.get("progress")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip(), "model_intent"
        arguments = action.get("arguments")
        purpose = (
            (isinstance(arguments, dict) and arguments.get("description"))
            or action.get("purpose")
            or action.get("description")
        )
        if isinstance(purpose, str) and purpose.strip():
            return purpose.strip(), "model_intent"
        action_type = str(action.get("type", "action"))
        labels = {"bash": "Run a workspace command", "final": "Deliver the final answer"}
        return labels.get(action_type, action_type.replace("_", " ").title()), "derived_summary"
    if event_name == "tool/call":
        return f"Use {event.get('tool', 'tool')}", "derived_summary"
    return "", None


def _action_command(action: dict[str, Any]) -> str | None:
    """Recorded bash actions are native tool_calls: ``action_to_dict()`` nests the
    command under ``arguments`` (``{"type": "bash", "id": ..., "arguments":
    {"command": ...}}``), not as a top-level field."""
    arguments = action.get("arguments")
    if isinstance(arguments, dict) and isinstance(arguments.get("command"), str):
        return arguments["command"]
    if isinstance(action.get("command"), str):
        return action["command"]
    return None


def _action_summary(action: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any] | None:
    if action is None and event.get("event") == "tool/call":
        return redact(
            {
                "type": "tool",
                "tool": event.get("tool"),
                "call_id": event.get("call_id"),
                "arguments": event.get("arguments"),
            }
        )
    if action is None:
        return None
    result: dict[str, Any] = {"type": action.get("type")}
    for key in ("purpose", "question", "name", "join"):
        if key in action:
            result[key] = action[key]
    command = _action_command(action)
    if isinstance(command, str):
        result["command"] = _truncate(command, MAX_COMMAND_CHARS)
    if action.get("type") == "final":
        result["answer"] = _truncate(str(action.get("answer", "")), 4_000)
    return redact(result)


def _observation_summary(event: dict[str, Any]) -> dict[str, Any] | None:
    event_name = str(event.get("event", ""))
    if event_name == "policy_decision":
        return redact(
            {
                "kind": "policy_decision",
                "decision": event.get("decision_type"),
                "policy": event.get("policy_name"),
                "message": event.get("reason"),
            }
        )
    if event_name == "tool/result":
        content = event.get("content")
        return {
            "kind": "tool_error" if event.get("is_error") else "tool_result",
            "success": not bool(event.get("is_error")),
            "duration_ms": event.get("duration_ms", 0),
            "preview": _truncate(
                json.dumps(redact(content), ensure_ascii=False), MAX_PREVIEW_CHARS
            ),
        }
    raw = event.get("observation")
    if isinstance(raw, dict):
        stdout = str(raw.get("stdout_preview", ""))
        stderr = str(raw.get("stderr_preview", ""))
        preview_source = (
            stderr if raw.get("kind") in {"protocol_error", "command_error"} and stderr else stdout
        )
        return redact(
            {
                "kind": raw.get("kind"),
                "success": raw.get("kind") in {"command_result", "no_output"},
                "exit_code": raw.get("exit_code"),
                "message": raw.get("message", ""),
                "duration_ms": raw.get("duration_ms", 0),
                "output_chars": len(stdout) + len(stderr),
                "output_lines": stdout.count("\n") + stderr.count("\n") + 1
                if stdout or stderr
                else 0,
                "preview": _truncate(preview_source, MAX_PREVIEW_CHARS),
                "artifact_ids": list(raw.get("artifact_ids", [])),
            }
        )
    if event_name in {"run_started", "run_completed", "run_failed", "run_interrupted"}:
        return redact(
            {
                "status": event_name.removeprefix("run_"),
                "goal": event.get("goal"),
                "message": event.get("state_summary") or event.get("final_answer"),
            }
        )
    if event_name in {"child_start", "child_result", "workflow_summary_observation"}:
        payload = event.get("result") or event.get("observation") or event
        return redact(_bounded_mapping(payload)) if isinstance(payload, dict) else None
    return None


def _bounded_mapping(value: dict[str, Any]) -> dict[str, Any]:
    omitted = {
        "response_preview",
        "stdout_preview",
        "stderr_preview",
        "cacheability",
        "event",
        "created_at",
    }
    return {
        str(key): _truncate(item, MAX_PREVIEW_CHARS) if isinstance(item, str) else item
        for key, item in value.items()
        if key not in omitted
    }


def _format_markdown(records: list[dict[str, Any]]) -> str:
    run_id = next(
        (str(record.get("run_id")) for record in records if record.get("run_id")), "unknown"
    )
    start = next((record for record in records if record.get("event") == "run_started"), None)
    end = next(
        (
            record
            for record in reversed(records)
            if record.get("event") in {"run_completed", "run_failed", "run_interrupted"}
        ),
        None,
    )
    goal = ((start or {}).get("observation") or {}).get("goal") or ""
    outcome = ((end or {}).get("observation") or {}).get("status") or "running"
    turns = {record.get("turn_id") for record in records if record.get("turn_id")}
    actions = sum(record.get("kind") == "action" for record in records)
    lines = [
        "# miniCC Run Transcript",
        "",
        "## Overview",
        "",
        f"- **Run:** `{run_id}`",
        f"- **Outcome:** `{outcome}`",
        f"- **Turns:** `{len(turns)}`",
        f"- **Actions:** `{actions}`",
    ]
    if goal:
        lines.append(f"- **Goal:** {goal}")
    lines.extend(["", "## Timeline", ""])
    active_turn: str | None = None
    for record in _collapse_repeated_errors(records):
        turn_id = record.get("turn_id")
        if turn_id and turn_id != active_turn:
            active_turn = str(turn_id)
            lines.extend([f"### {active_turn.replace('-', ' ').title()}", ""])
        lines.extend(_format_record(record))
    lines.extend(
        [
            "---",
            "",
            "Full low-level events and unabridged command output remain in `trace.jsonl` and linked artifacts.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_record(record: dict[str, Any]) -> list[str]:
    kind = record.get("kind")
    event = record.get("event")
    intent = str(record.get("intent") or "")
    raw_action = record.get("action")
    action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
    raw_observation = record.get("observation")
    observation: dict[str, Any] = raw_observation if isinstance(raw_observation, dict) else {}
    if event == "run_started":
        return ["**Run started**", ""]
    if event in {"run_completed", "run_failed", "run_interrupted"}:
        label = {
            "run_completed": "Run completed",
            "run_failed": "Run failed",
            "run_interrupted": "Run interrupted",
        }[str(event)]
        message = observation.get("message")
        return [f"**{label}**" + (f": {message}" if message else ""), ""]
    if kind == "action":
        action_type = str(action.get("type") or action.get("tool") or "action")
        lines = [
            f"**Intent:** {intent}" if intent else "**Intent:** Perform the next action",
            "",
            f"**Action:** `{action_type}`",
        ]
        command = _action_command(action)
        if command:
            lines.extend(["", "```shell", str(command), "```"])
        if action.get("answer"):
            lines.extend(["", str(action["answer"])])
        lines.append("")
        return lines
    if kind == "observation":
        marker = "Succeeded" if observation.get("success") else "Failed"
        headline = f"**Observation:** {marker}{_duration(observation.get('duration_ms'))}{_output_size(observation)}"
        repeat_count = int(record.get("repeat_count") or 1)
        if repeat_count > 1:
            headline += f"; repeated {repeat_count} times through {record.get('repeat_last_turn')}"
        message = str(observation.get("message") or "").strip()
        if message and message not in {"Command exited successfully.", "Ordered tool results."}:
            headline += f" - {message}"
        lines = [headline, ""]
        preview = str(observation.get("preview") or "").strip()
        if preview:
            lines.extend(
                [
                    "<details>",
                    "<summary>Output preview</summary>",
                    "",
                    "```text",
                    preview,
                    "```",
                    "",
                    "</details>",
                    "",
                ]
            )
        return lines
    if kind == "policy":
        return [
            f"**Policy:** `{observation.get('decision')}` by `{observation.get('policy')}` - {observation.get('message')}",
            "",
        ]
    if kind in {"child_start", "child_end", "summary"}:
        label = {
            "child_start": "Child started",
            "child_end": "Child finished",
            "summary": "Workflow Summary",
        }[str(kind)]
        summary = observation.get("summary") or observation.get("status") or ""
        return [f"**{label}:** {summary}", ""]
    return []


def _collapse_repeated_errors(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collapsed: list[dict[str, Any]] = []
    for record in records:
        raw_observation = record.get("observation")
        observation: dict[str, Any] | None = (
            raw_observation if isinstance(raw_observation, dict) else None
        )
        is_error = (
            record.get("kind") == "observation"
            and isinstance(observation, dict)
            and not observation.get("success")
        )
        if is_error and collapsed:
            assert observation is not None
            previous = collapsed[-1]
            previous_observation = previous.get("observation")
            if (
                previous.get("kind") == "observation"
                and isinstance(previous_observation, dict)
                and observation.get("kind") == previous_observation.get("kind")
                and observation.get("message") == previous_observation.get("message")
            ):
                previous["repeat_count"] = int(previous.get("repeat_count") or 1) + 1
                previous["repeat_last_turn"] = record.get("turn_id")
                continue
        collapsed.append(dict(record))
    return collapsed


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n... [truncated {len(value) - limit} chars]"


def _duration(value: Any) -> str:
    try:
        milliseconds = int(value or 0)
    except (TypeError, ValueError):
        return ""
    return f" in {milliseconds / 1000:.2f}s" if milliseconds else ""


def _output_size(observation: dict[str, Any]) -> str:
    chars = int(observation.get("output_chars") or 0)
    lines = int(observation.get("output_lines") or 0)
    return f"; {lines} lines, {chars:,} chars" if chars else ""
