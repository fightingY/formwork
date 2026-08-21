"""Stable, redacted human-readable projection of V4 trace events."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)=([^\s&]+)")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET.sub(r"\1=[REDACTED]", value)
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items() if str(key).lower() not in {"authorization", "api_key", "token", "password", "secret"}}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class TranscriptProjector:
    def __init__(self, *, root_run_id: str = "") -> None:
        self.root_run_id = root_run_id
        self.records: list[dict[str, Any]] = []

    def project_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_name = str(event.get("event", "status"))
        kind = {
            "run_started": "status", "model_response": "observation", "action_started": "action",
            "tool/call": "action", "tool/result": "observation", "observation_created": "observation",
            "checkpoint_created": "status", "run_finished": "status", "child_start": "child_start",
            "child_result": "child_end", "workflow_summary_observation": "summary",
        }.get(event_name, "status")
        action = event.get("action")
        intent = ""
        intent_kind = None
        if isinstance(action, dict):
            intent = str(action.get("intent", ""))
            if intent:
                intent_kind = "model_intent"
        if not intent and kind == "action":
            intent = event_name.replace("_", " ")
            intent_kind = "derived_summary"
        record = {
            "schema_version": 1, "root_run_id": self.root_run_id or event.get("run_id", ""),
            "run_id": event.get("run_id", ""), "parent_run_id": event.get("parent_run_id"),
            "workflow_id": event.get("workflow_id"), "task_id": event.get("task_id"),
            "role": event.get("role"), "turn_id": event.get("turn_id"), "step_id": event.get("step_id"),
            "span_id": event.get("span_id"), "parent_span_id": event.get("parent_span_id"),
            "kind": kind, "timestamp": event.get("created_at"),
            "intent": intent, "intent_kind": intent_kind, "action": redact(action),
            "observation": redact(event.get("observation") or event.get("content")),
            "links": list(event.get("links", [])),
        }
        self.records.append(record)
        return record

    def project(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.project_event(event) for event in events]

    def write(self, run_dir: Path) -> tuple[Path, Path]:
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "transcript.jsonl"
        md_path = run_dir / "transcript.md"
        json_path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in self.records), encoding="utf-8")
        lines = ["# miniCC Transcript", ""]
        for record in self.records:
            label = record["kind"].replace("_", " ").title()
            detail = record.get("intent") or ""
            if record.get("observation"):
                detail = detail or json.dumps(record["observation"], ensure_ascii=False)
            lines.append(f"- **{label}** `{record.get('run_id', '')}` {detail}".rstrip())
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return json_path, md_path


def project_trace(trace_path: Path, run_dir: Path | None = None) -> tuple[Path, Path]:
    events = []
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
