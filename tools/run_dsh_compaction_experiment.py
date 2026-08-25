"""Run a small local scorecard for the DSH-style compaction path.

The experiment is intentionally self-contained: it uses deterministic long
trajectories and a deterministic provider that can inject CONTEXT_OVERFLOW.
It exercises the current ``ContextBuilder`` and ``AgentLoop`` contracts and
writes an interview-friendly JSON/Markdown scorecard under ``.minicc``.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicc.core.context import ContextBuilder, ContextConfig, _estimate_messages_tokens
from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig
from minicc.core.protocol import BashAction
from minicc.core.provider import (
    CONTEXT_OVERFLOW,
    CompletionOptions,
    LlmFailure,
    ModelResponse,
    ModelUsage,
    ProviderError,
)
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState, TrajectoryStep

WINDOW = 20_000
THRESHOLD_RATIO = 0.80
RETAIN_RATIO = 0.16
MARKERS = (
    "ROOT_CAUSE=AUTH_HEADER",
    "PATCH_FILE=src/service.py",
    "VERIFY_CMD=python -m unittest",
    "FINAL_VERIFIER=PASS",
)


@dataclass(frozen=True)
class Workload:
    name: str
    steps: int
    payload_chars: int


WORKLOADS = (
    Workload("debug_trace", 28, 2_200),
    Workload("tool_log", 32, 2_500),
    Workload("refactor_review", 26, 2_000),
    Workload("mixed_failure", 30, 2_350),
)


def _step(workload: Workload, index: int, repeat: int) -> TrajectoryStep:
    marker = MARKERS[index % len(MARKERS)]
    action = BashAction(
        command=f"printf 'workload={workload.name} step={index} repeat={repeat}'",
        purpose=f"inspect and verify {marker}",
    )
    body = (
        f"{marker} | workload={workload.name} | repeat={repeat} | step={index} | "
        f"authoritative observation. Required facts: {'; '.join(MARKERS)}. "
    )
    padding = ("stable evidence line; " * ((workload.payload_chars // 22) + 2))[: workload.payload_chars]
    observation = Observation(
        kind="command_result",
        exit_code=0,
        message=body + padding,
        stdout_preview=body + padding,
        artifact_ids=[f"artifact-{workload.name}-{repeat}-{index}"],
    )
    return TrajectoryStep(action=action, observation=observation, state_snapshot=f"state={marker}")


def _config(strategy: str, *, recovery: bool = False) -> ContextConfig:
    return ContextConfig(
        max_prompt_chars=120_000,
        recent_turns=0,
        artifact_preview_chars=1_600,
        summary_max_chars=650,
        field_preview_chars=1_200,
        compaction_strategy=strategy,
        retention_markers=MARKERS,
        prompt_layout="append_until_compaction",
        context_window=WINDOW,
        threshold_ratio=1.0 if recovery else THRESHOLD_RATIO,
        retain_ratio=RETAIN_RATIO,
        max_overflow_retries=1,
    )


def _context_arm(workload: Workload, repeat: int, strategy: str) -> dict[str, Any]:
    builder = ContextBuilder(_config(strategy))
    state = RunState.start(f"Complete the {workload.name} coding task")
    trajectory = [_step(workload, index, repeat) for index in range(1, workload.steps + 1)]
    prompt_tokens: list[int] = []
    post_compaction_tokens: list[int] = []

    for end in range(1, len(trajectory) + 1):
        prefix = trajectory[:end]
        before = int(state.metrics.get("context_compactions", 0))
        builder.maybe_compact(state, prefix)
        messages = builder.build_messages(state, prefix)
        current = _estimate_messages_tokens(messages)
        prompt_tokens.append(current)
        if int(state.metrics.get("context_compactions", 0)) > before:
            post_compaction_tokens.append(current)

    final_messages = builder.build_messages(state, trajectory)
    final_text = "\n".join(message["content"] for message in final_messages)
    threshold = int(WINDOW * (THRESHOLD_RATIO if strategy != "disabled" else 1.0))
    retained = sum(marker in final_text for marker in MARKERS)
    return {
        "strategy": strategy,
        "peak_prompt_tokens": max(prompt_tokens, default=0),
        "total_prompt_tokens": sum(prompt_tokens),
        "prompt_samples": len(prompt_tokens),
        "compactions": int(state.metrics.get("context_compactions", 0)),
        "compacted_steps": int(state.metrics.get("context_compacted_steps", 0)),
        "post_compaction_peak_tokens": max(post_compaction_tokens, default=0),
        "budget_adherence": bool(post_compaction_tokens)
        and all(value <= threshold for value in post_compaction_tokens),
        "threshold_tokens": threshold,
        "facts_expected": len(MARKERS),
        "facts_preserved": retained,
        "fact_retention_rate": retained / len(MARKERS),
        "task_continuity": retained == len(MARKERS) and "FINAL_VERIFIER=PASS" in final_text,
        "context_budget_overflows": int(state.metrics.get("context_budget_overflows", 0)),
        "prompt_chars_saved": int(state.metrics.get("context_compaction_chars_saved", 0)),
    }


class _RecoveryExecutor(BashExecutor):
    def __init__(self, payload_chars: int) -> None:
        self.payload_chars = payload_chars

    def run(self, action: BashAction, state: RunState) -> Observation:
        marker = MARKERS[(int(state.metrics.get("bash_actions", 0))) % len(MARKERS)]
        body = f"{marker} recovery evidence " + ("log-line; " * (self.payload_chars // 9))
        return Observation(
            kind="command_result",
            exit_code=0,
            message=body,
            stdout_preview=body,
            artifact_ids=["recovery-artifact"],
        )


class _OverflowProvider:
    def __init__(self, steps_before_overflow: int) -> None:
        self.steps_before_overflow = steps_before_overflow
        self.calls = 0

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        del messages, options
        self.calls += 1
        if self.calls <= self.steps_before_overflow:
            return ModelResponse(
                text='{"type":"bash","command":"printf recovery","purpose":"continue"}',
                raw={},
                usage=ModelUsage(),
                latency_ms=1,
            )
        if self.calls == self.steps_before_overflow + 1:
            raise ProviderError(
                failure=LlmFailure(message="context window exceeded", code=CONTEXT_OVERFLOW)
            )
        return ModelResponse(
            text='{"type":"final","answer":"FINAL_VERIFIER=PASS"}',
            raw={},
            usage=ModelUsage(),
            latency_ms=1,
        )


def _overflow_arm(payload_chars: int, strategy: str, root: Path) -> dict[str, Any]:
    provider = _OverflowProvider(steps_before_overflow=10)
    builder = ContextBuilder(_config(strategy, recovery=True))
    state = RunState.start("Recover from a context overflow")
    result = AgentLoop(
        provider,
        _RecoveryExecutor(payload_chars),
        context_builder=builder,
        session=SessionManager(runs_root=root / strategy),
        config=LoopConfig(max_turns=12),
    ).run(state)
    metrics = result.state.metrics
    recovered = int(metrics.get("context_overflow_recovered", 0))
    return {
        "strategy": strategy,
        "status": result.state.status,
        "provider_calls": provider.calls,
        "overflow_retries": int(metrics.get("context_overflow_retries", 0)),
        "overflow_recovered": recovered,
        "recovery_success": result.state.status == "completed" and recovered == 1,
        "compactions": int(metrics.get("context_compactions", 0)),
    }


def _paired_group(workload: Workload, repeat: int, root: Path) -> dict[str, Any]:
    baseline = _context_arm(workload, repeat, "disabled")
    compacted = _context_arm(workload, repeat, "deterministic")
    baseline_recovery = _overflow_arm(workload.payload_chars // 2, "disabled", root)
    compacted_recovery = _overflow_arm(workload.payload_chars // 2, "deterministic", root)
    peak_reduction = 1 - compacted["peak_prompt_tokens"] / max(baseline["peak_prompt_tokens"], 1)
    total_reduction = 1 - compacted["total_prompt_tokens"] / max(baseline["total_prompt_tokens"], 1)
    return {
        "group": f"{workload.name}-r{repeat}",
        "workload": workload.name,
        "repeat": repeat,
        "baseline": baseline,
        "compacted": compacted,
        "baseline_overflow": baseline_recovery,
        "compacted_overflow": compacted_recovery,
        "peak_prompt_reduction_rate": peak_reduction,
        "total_prompt_reduction_rate": total_reduction,
        "passed": (
            compacted["compactions"] > 0
            and compacted["budget_adherence"]
            and compacted["fact_retention_rate"] == 1.0
            and compacted["task_continuity"]
            and compacted_recovery["recovery_success"]
            and not baseline_recovery["recovery_success"]
            and peak_reduction > 0
            and total_reduction > 0
        ),
    }


def run_experiment(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="minicc-dsh-compaction-") as temporary:
        root = Path(temporary)
        groups = [
            _paired_group(workload, repeat, root)
            for workload in WORKLOADS
            for repeat in range(1, 4)
        ]
    passed_groups = sum(bool(group["passed"]) for group in groups)
    compacted = [group["compacted"] for group in groups]
    compacted_overflow = [group["compacted_overflow"] for group in groups]
    report: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "dsh_compaction_local_12",
        "status": "PASS" if passed_groups == len(groups) else "FAIL",
        "groups": len(groups),
        "passed_groups": passed_groups,
        "window_tokens": WINDOW,
        "threshold_ratio": THRESHOLD_RATIO,
        "retain_ratio": RETAIN_RATIO,
        "workloads": [workload.name for workload in WORKLOADS],
        "metrics": {
            "peak_prompt_reduction_rate_mean": sum(
                1 - group["compacted"]["peak_prompt_tokens"] / max(group["baseline"]["peak_prompt_tokens"], 1)
                for group in groups
            ) / len(groups),
            "total_prompt_reduction_rate_mean": sum(group["total_prompt_reduction_rate"] for group in groups) / len(groups),
            "budget_adherence_rate": sum(item["budget_adherence"] for item in compacted) / len(compacted),
            "fact_retention_rate": sum(item["fact_retention_rate"] for item in compacted) / len(compacted),
            "task_continuity_rate": sum(item["task_continuity"] for item in compacted) / len(compacted),
            "overflow_recovery_rate": sum(item["recovery_success"] for item in compacted_overflow) / len(compacted_overflow),
            "baseline_overflow_recovery_rate": sum(item["recovery_success"] for item in (group["baseline_overflow"] for group in groups)) / len(groups),
            "compaction_trigger_rate": sum(item["compactions"] > 0 for item in compacted) / len(compacted),
        },
        "groups_detail": groups,
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    (output_dir / "summary.md").write_text(_summary(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# DSH-style Context Compaction Local Scorecard",
        "",
        f"Status: **{report['status']}** ({report['passed_groups']}/{report['groups']} paired groups)",
        "",
        f"- Context window: `{report['window_tokens']}` tokens; threshold: `{report['threshold_ratio']:.0%}`; retain tail: `{report['retain_ratio']:.0%}`",
        f"- Peak prompt reduction: **{metrics['peak_prompt_reduction_rate_mean']:.2%}** mean",
        f"- Total prompt reduction: **{metrics['total_prompt_reduction_rate_mean']:.2%}** mean",
        f"- Post-compaction budget adherence: **{metrics['budget_adherence_rate']:.0%}**",
        f"- Critical fact retention: **{metrics['fact_retention_rate']:.0%}**",
        f"- Task continuity: **{metrics['task_continuity_rate']:.0%}**",
        f"- Overflow recovery: **{metrics['overflow_recovery_rate']:.0%}** (baseline: {metrics['baseline_overflow_recovery_rate']:.0%})",
        f"- Compaction trigger coverage: **{metrics['compaction_trigger_rate']:.0%}**",
        "",
        "| Group | Peak reduction | Total reduction | Compactions | Post-budget | Facts | Overflow recovery |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in report["groups_detail"]:
        compacted = group["compacted"]
        lines.append(
            f"| `{group['group']}` | {group['peak_prompt_reduction_rate']:.2%} | "
            f"{group['total_prompt_reduction_rate']:.2%} | {compacted['compactions']} | "
            f"{'PASS' if compacted['budget_adherence'] else 'FAIL'} | "
            f"{compacted['fact_retention_rate']:.0%} | "
            f"{'PASS' if group['compacted_overflow']['recovery_success'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "The scorecard uses fixed local fixtures and the current ContextBuilder/AgentLoop contracts.",
            "",
        ]
    )
    return "\n".join(lines)


def _summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    return "\n".join(
        [
            "# Private Local Context Compaction Showcase v4",
            "",
            "Provider: local deterministic ContextBuilder/AgentLoop harness",
            "",
            f"Configuration: context window `{report['window_tokens']:,}` tokens, threshold `80%`, retained tail `16%`, "
            "four long-trajectory workloads, three repeats per workload.",
            "",
            "## Result",
            "",
            f"- Paired groups: `{report['passed_groups']}/{report['groups']} PASS`",
            f"- Peak prompt-token reduction: `{metrics['peak_prompt_reduction_rate_mean']:.2%}`",
            f"- Cumulative prompt-token reduction: `{metrics['total_prompt_reduction_rate_mean']:.2%}`",
            f"- Post-compaction budget adherence: `{metrics['budget_adherence_rate']:.0%}`",
            f"- Critical fact retention: `{metrics['fact_retention_rate']:.0%}`",
            f"- Task continuity: `{metrics['task_continuity_rate']:.0%}`",
            f"- Context-overflow recovery: `{metrics['overflow_recovery_rate']:.0%}`",
            f"- Disabled baseline overflow recovery: `{metrics['baseline_overflow_recovery_rate']:.0%}`",
            "",
            "## Workload groups",
            "",
            "- Debug trace: `debug_trace-r1..r3`",
            "- Tool log: `tool_log-r1..r3`",
            "- Refactor review: `refactor_review-r1..r3`",
            "- Mixed failure: `mixed_failure-r1..r3`",
            "",
            "The scorecard is the local evidence source for the Context Compaction section in "
            "`面试/上下文压缩机制与面试准备.md`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".minicc/private_compaction_showcase_v4"),
    )
    args = parser.parse_args()
    report = run_experiment(args.output_dir)
    print(f"status: {report['status']}")
    print(f"groups: {report['passed_groups']}/{report['groups']}")
    print(f"report: {args.output_dir.resolve() / 'report.md'}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
