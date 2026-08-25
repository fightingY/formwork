from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minicc.core.ledger import LEDGER_SCHEMA_VERSION
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
        "schema_version": LEDGER_SCHEMA_VERSION,
        "entity_type": "run_report",
        "run_id": state.run_id,
        "suite_id": state.suite_id,
        "milestone": state.milestone,
        "stage": state.stage,
        "goal": state.goal,
        "status": state.status,
        "result": (
            "PASS"
            if state.status == "completed"
            else "FAIL"
            if state.status == "failed"
            else "UNKNOWN"
        ),
        "task_success": None,
        "agent_success": state.status == "completed" if state.status in {"completed", "failed"} else None,
        "infrastructure_success": (
            int(state.metrics.get("provider_errors", 0)) == 0
            and int(state.metrics.get("infrastructure_errors", 0)) == 0
        ),
        "policy_outcome": "denied" if int(state.metrics.get("policy_denials", 0)) else "clear",
        "passed": state.status == "completed",
        "final_answer": state.final_answer,
        "state_summary": state.state_summary,
        "metrics": dict(state.metrics),
        "evidence": {
            "state": _evidence_path(state.run_dir, "state.json"),
            "trace": _evidence_path(state.run_dir, "trace.jsonl"),
            "metrics": _evidence_path(state.run_dir, "metrics.json"),
            "diff": _evidence_path(artifacts_dir, "diff.patch"),
            "workspace_manifest": _evidence_path(state.run_dir, "workspace_manifest.json"),
            "latest_checkpoint": _evidence_path(state.run_dir, "checkpoints/latest.json"),
        },
    }


def format_run_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    evidence = report["evidence"]
    lines = [
        "# miniCC 运行报告",
        "",
        f"- Run：`{report['run_id']}`",
        f"- 状态：`{report['status']}`",
        f"- 通过：`{'是' if report['passed'] else '否'}`",
        f"- 模型回合：`{metrics.get('turns', 0)}`",
        f"- Bash actions：`{metrics.get('bash_actions', 0)}`",
        f"- Checkpoints：`{metrics.get('checkpoints_created', 0)}`",
        f"- Resume 次数：`{metrics.get('resumes_completed', 0)}`",
        f"- Policy denials：`{metrics.get('policy_denials', 0)}`",
        f"- 审批请求：`{metrics.get('approvals_requested', 0)}`",
        "",
        "## 目标",
        "",
        str(report["goal"]),
        "",
        "## 证据",
        "",
        f"- State：`{evidence['state']}`",
        f"- Trace：`{evidence['trace']}`",
        f"- Metrics：`{evidence['metrics']}`",
        f"- Diff：`{evidence['diff']}`",
        f"- Workspace manifest：`{evidence['workspace_manifest']}`",
        f"- 最新 checkpoint：`{evidence['latest_checkpoint']}`",
    ]
    if report.get("final_answer"):
        lines.extend(["", "## 最终回答", "", str(report["final_answer"])])
    if report.get("state_summary"):
        lines.extend(["", "## 状态摘要", "", str(report["state_summary"])])
    lines.append("")
    return "\n".join(lines)


def _evidence_path(parent: Path | None, name: str) -> str:
    if parent is None:
        return name
    return str(parent / name)
