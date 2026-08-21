from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from minicc.core.protocol import ToolCall, ToolCallsAction, parse_action
from minicc.core.state import RunState
from minicc.core.tooling import ExecutionMode, FileSystemCapability, ToolCallScheduler, ToolResult
from minicc.trace.recorder import TraceRecorder


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic V3.6 M4 offline evidence.")
    parser.add_argument("--output", type=Path, default=Path("acceptance/stable-v3.6"))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite immutable evidence: {output}")

    temp_root = Path(tempfile.mkdtemp(prefix="minicc-v36-evidence-"))
    try:
        evidence = _run_checks(temp_root)
        _write_bundle(output, evidence)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    print(json.dumps({"output": str(output), "result": "PASS", "provider_calls": 0}, indent=2))
    return 0


def _run_checks(root: Path) -> dict[str, Any]:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (workspace / "b.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    state = RunState.start(
        "V3.6 M4 offline evidence",
        workspace_host_path=workspace,
        suite_id="v3.6-m4-offline",
        milestone="v3.6",
        stage="offline_evidence",
    )
    state.metrics.update(
        {
            "profile": "hybrid-v3.6",
            "max_parallel_tool_calls": 4,
            "max_tool_calls_per_step": 16,
        }
    )
    trace = TraceRecorder()
    parsed = parse_action(
        json.dumps(
            {
                "type": "tool_calls",
                "calls": [
                    {"id": "r1", "tool": "read", "arguments": {"path": "a.txt", "limit": 2}},
                    {"id": "r2", "tool": "read", "arguments": {"path": "b.txt"}},
                ],
            }
        )
    )
    assert isinstance(parsed, ToolCallsAction)
    fs = FileSystemCapability(max_read_lines=400)
    scheduler = ToolCallScheduler(fs, max_parallel_tool_calls=4)
    for order, call in enumerate(parsed.calls):
        trace.tool_call(
            state,
            call_id=call.id,
            tool=call.tool,
            arguments=dict(call.arguments),
            model_order=order,
            execution_mode="parallel",
        )
    read_results = scheduler.dispatch(parsed, state)
    for result in read_results:
        trace.tool_result(state, result)
    assert [item.call_id for item in read_results] == ["r1", "r2"]
    assert read_results[0].content["content"].startswith("1: one")

    hash_value = str(read_results[0].content["sha256"])
    edit = fs.run(
        ToolCall(
            "e1",
            "edit",
            {
                "path": "a.txt",
                "old_string": "two",
                "new_string": "TWO",
                "expected_hash": hash_value,
            },
        ),
        state,
    )
    assert not edit.is_error and "-two" in str(edit.content["diff"])
    write = fs.run(ToolCall("w1", "write", {"path": "new.txt", "content": "created\n"}), state)
    assert not write.is_error and write.content["created"] is True
    conflict = fs.run(
        ToolCall(
            "e2",
            "edit",
            {
                "path": "a.txt",
                "old_string": "TWO",
                "new_string": "two",
                "expected_hash": "sha256:stale",
            },
        ),
        state,
    )
    assert conflict.is_error and conflict.content["error_code"] == "EDIT_VERSION_CONFLICT"

    class RecordingRunner:
        def execution_mode(self, call: ToolCall) -> ExecutionMode:
            return "parallel" if call.tool == "read" else "exclusive"

        def run(self, call: ToolCall, state: RunState) -> ToolResult:
            return ToolResult(call.id, call.tool, 0, self.execution_mode(call), {"ok": True})

    ordered = ToolCallScheduler(RecordingRunner(), max_parallel_tool_calls=1).dispatch(
        ToolCallsAction(
            (
                ToolCall("p1", "read", {"path": "a.txt"}),
                ToolCall("x1", "write", {"path": "new.txt", "content": "x"}),
                ToolCall("p2", "read", {"path": "b.txt"}),
            )
        ),
        state,
    )
    assert [item.call_id for item in ordered] == ["p1", "x1", "p2"]

    trace_payload = trace.events
    assert sum(event["event"] == "tool/call" for event in trace_payload) == 2
    assert sum(event["event"] == "tool/result" for event in trace_payload) == 2
    return {
        "schema_version": 1,
        "entity_type": "v3.6_offline_evidence",
        "milestone": "v3.6",
        "stage": "M4",
        "result": "PASS",
        "provider_calls": 0,
        "profile": "hybrid-v3.6",
        "max_parallel_tool_calls": 4,
        "checks": {
            "protocol_envelope": "PASS",
            "read_window_hash": "PASS",
            "edit_hash_conflict": "PASS",
            "atomic_write_new_file": "PASS",
            "ordered_barrier_results": "PASS",
            "trace_call_result_pairs": "PASS",
        },
        "trace": trace_payload,
    }


def _write_bundle(output: Path, evidence: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.parent / f".{output.name}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir()
    evidence_path = temp / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "entity_type": "v3.6_m4_report",
        "milestone": "v3.6",
        "stage": "M4",
        "status": "PASS",
        "passed": True,
        "provider_calls": 0,
        "profile": "hybrid-v3.6",
        "evidence": "evidence.json",
        "replay_command": "uv run python tools/run_v36_offline_evidence.py --output acceptance/stable-v3.6-replay",
        "m5": {
            "status": "EXPLORATORY_ONLY",
            "formal_ab_runs": 0,
            "reason": "Formal two-round baseline/hybrid A/B remains a separate provider-budgeted stage.",
        },
    }
    report_path = temp / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (temp / "report.md").write_text(
        "# Stable V3.6 M4 Offline Evidence\n\n"
        "Deterministic protocol, filesystem capability, ordered scheduler, barrier, and trace checks.\n\n"
        "- Result: `PASS`\n- Provider calls: `0`\n- Profile: `hybrid-v3.6`\n"
        "- M5 formal A/B: `EXPLORATORY_ONLY` / not part of this offline archive\n",
        encoding="utf-8",
    )
    artifacts = {}
    for path in (evidence_path, report_path, temp / "report.md"):
        artifacts[path.name] = {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "schema_version": 1,
        "entity_type": "v3.6_m4_manifest",
        "milestone": "v3.6",
        "stage": "M4",
        "status": "PASS",
        "provider_calls": 0,
        "artifacts": artifacts,
    }
    (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(output)


if __name__ == "__main__":
    raise SystemExit(main())
