from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minicc.core.state import RunState


def write_run_report(state: RunState) -> tuple[Path, Path] | None:
    if state.run_dir is None:
        return None

    state.run_dir.mkdir(parents=True, exist_ok=True)
    json_path = state.run_dir / "run_report.json"
    markdown_path = state.run_dir / "run_report.md"
    report = run_report_snapshot(state)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(format_run_report(report), encoding="utf-8")
    return json_path, markdown_path


def run_report_snapshot(state: RunState) -> dict[str, Any]:
    artifacts_dir = state.artifacts_dir or (state.run_dir / "artifacts" if state.run_dir else None)
    return {
        "run_id": state.run_id,
        "goal": state.goal,
        "status": state.status,
        "passed": state.status == "completed",
        "final_answer": state.final_answer,
        "state_summary": state.state_summary,
        "metrics": dict(state.metrics),
        "evidence": {
            "state": _evidence_path(state.run_dir, "state.json"),
            "trace": _evidence_path(state.run_dir, "trace.jsonl"),
            "metrics": _evidence_path(state.run_dir, "metrics.json"),
            "diff": _evidence_path(artifacts_dir, "diff.patch"),
        },
    }


def format_run_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    evidence = report["evidence"]
    lines = [
        "# miniCC run report",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Status: `{report['status']}`",
        f"- Passed: `{'true' if report['passed'] else 'false'}`",
        f"- Turns: `{metrics.get('turns', 0)}`",
        f"- Bash actions: `{metrics.get('bash_actions', 0)}`",
        f"- Policy denials: `{metrics.get('policy_denials', 0)}`",
        f"- Approvals requested: `{metrics.get('approvals_requested', 0)}`",
        "",
        "## Goal",
        "",
        str(report["goal"]),
        "",
        "## Evidence",
        "",
        f"- State: `{evidence['state']}`",
        f"- Trace: `{evidence['trace']}`",
        f"- Metrics: `{evidence['metrics']}`",
        f"- Diff: `{evidence['diff']}`",
    ]
    if report.get("final_answer"):
        lines.extend(["", "## Final Answer", "", str(report["final_answer"])])
    if report.get("state_summary"):
        lines.extend(["", "## State Summary", "", str(report["state_summary"])])
    lines.append("")
    return "\n".join(lines)


def _evidence_path(parent: Path | None, name: str) -> str:
    if parent is None:
        return name
    return str(parent / name)
