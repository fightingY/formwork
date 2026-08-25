"""Run a controlled, interview-facing evaluation/replay showcase.

The harness uses the real AgentLoop, policy chain, trace recorder, workspace
snapshot, verifier and replay implementation. The only controlled component is
the model provider: scripted responses remove network/model variance so this
experiment measures the harness contracts rather than provider randomness.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from minicc import cli
from minicc.config import (
    BudgetSettings,
    ContextSettings,
    PolicySettings,
    ProjectSettings,
    ProviderRoute,
    SandboxSettings,
    Settings,
    ToolingSettings,
)
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.core.session import SessionManager
from minicc.core.state import RunState, save_run_state
from minicc.core.verification import CommandCompletionVerifier
from minicc.evals.case import discover_cases
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.docker_runner import (
    DockerCommandExecutor,
    DockerSandboxConfig,
    DockerSandboxRunner,
)
from minicc.sandbox.local_runner import LocalCommandExecutor
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff
from minicc.trace.metrics import write_metrics
from minicc.trace.replay import compare_fresh_replay, create_replay_case, run_deterministic_replay
from minicc.trace.report import write_run_report
from minicc.trace.transcript import project_trace

REPEAT_DETERMINISTIC = 5
REPEAT_FRESH = 3


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    goal: str
    responses: tuple[str, ...]
    verifier: str
    case_dir: Path
    fixture_dir: Path


def load_scenarios(cases_root: Path) -> tuple[Scenario, ...]:
    scenarios: list[Scenario] = []
    for case in discover_cases(cases_root):
        raw = yaml.safe_load((case.case_dir / "case.yaml").read_text(encoding="utf-8")) or {}
        showcase = raw.get("showcase") or {}
        responses = showcase.get("responses") or []
        if not isinstance(responses, list) or not responses or not all(isinstance(item, str) for item in responses):
            raise ValueError(f"{case.case_dir / 'case.yaml'} must define showcase.responses")
        verifier = str(showcase.get("verifier") or "").strip()
        if not verifier:
            raise ValueError(f"{case.case_dir / 'case.yaml'} must define showcase.verifier")
        scenarios.append(
            Scenario(
                name=case.name,
                title=str(showcase.get("title") or case.name),
                goal=case.prompt,
                responses=tuple(responses),
                verifier=verifier,
                case_dir=case.case_dir,
                fixture_dir=case.fixture_dir,
            )
        )
    return tuple(scenarios)


class ScriptedProvider:
    provider_name = "replay-showcase-scripted-provider"

    def __init__(self, responses: tuple[str, ...]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        options: CompletionOptions | None = None,
    ) -> ModelResponse:
        del messages, options
        if not self.responses:
            raise AssertionError("showcase scripted provider was called after its fixture ended")
        self.calls += 1
        return ModelResponse(
            text=self.responses.pop(0),
            raw={"model": "scripted-showcase"},
            usage=ModelUsage(prompt_tokens=128, completion_tokens=32, total_tokens=160),
            latency_ms=1,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("面试") / "评测与重放实验",
        help="Interview evidence output directory.",
    )
    parser.add_argument(
        "--deterministic-repeat",
        type=int,
        default=REPEAT_DETERMINISTIC,
        help="Deterministic replay repetitions per scenario.",
    )
    parser.add_argument(
        "--fresh-repeat",
        type=int,
        default=REPEAT_FRESH,
        help="Fresh replay repetitions per scenario.",
    )
    parser.add_argument(
        "--cases-root",
        type=Path,
        default=Path("eval_cases") / "real_project_replay_v1",
        help="Visible eval-case root used by the showcase.",
    )
    parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Use local execution even when Docker is available.",
    )
    parser.add_argument(
        "--docker-image",
        default=None,
        help="Docker image override for source/fresh runs.",
    )
    args = parser.parse_args()
    if args.deterministic_repeat <= 0 or args.fresh_repeat <= 0:
        parser.error("repeat counts must be positive")

    scenarios = load_scenarios(args.cases_root.resolve())
    if not scenarios:
        parser.error(f"no eval cases found under {args.cases_root}")
    docker_enabled = False if args.execute_local else _docker_available()
    output_dir = args.output_dir.resolve()
    evidence_root = output_dir / "evidence"
    if evidence_root.exists():
        _remove_tree(evidence_root)
    evidence_root.mkdir(parents=True, exist_ok=True)
    runtime_root = evidence_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    settings = _showcase_settings(docker_image=args.docker_image)

    scenario_rows: list[dict[str, Any]] = []
    deterministic_rows: list[dict[str, Any]] = []
    fresh_rows: list[dict[str, Any]] = []
    tamper_rows: list[dict[str, Any]] = []
    durations: list[float] = []

    for scenario in scenarios:
        source_fixture = scenario.fixture_dir
        source_run = _run_scenario(
            scenario,
            source_fixture,
            runtime_root / "runs",
            settings,
            run_label="source",
            docker_enabled=docker_enabled,
        )
        case_dir = create_replay_case(
            source_run,
            output_dir=evidence_root / "cases",
            overwrite=True,
        )

        deterministic_passes = 0
        deterministic_case_reports: list[str] = []
        for repeat in range(1, args.deterministic_repeat + 1):
            started = time.perf_counter()
            result = run_deterministic_replay(case_dir, output_dir=case_dir)
            durations.append(time.perf_counter() - started)
            deterministic_passes += int(result.passed)
            deterministic_case_reports.append(str(result.report_path))
            deterministic_rows.append(
                {
                    "scenario": scenario.name,
                    "repeat": repeat,
                    "passed": result.passed,
                    "evidence_complete": all(
                        result.report["checks"].get(name, {}).get("passed") is True
                        for name in (
                            "lifecycle",
                            "model_response_fixtures",
                            "action_parse_coverage",
                            "tool_fixtures",
                            "artifact_manifest",
                        )
                    ),
                    "report": str(result.report_path),
                }
            )

        fresh_passes = 0
        fresh_action_matches = 0
        fresh_diff_matches = 0
        fresh_reports: list[str] = []
        for repeat in range(1, args.fresh_repeat + 1):
            fresh_run = _run_scenario(
                scenario,
                case_dir / "workspace",
                runtime_root / "fresh-runs",
                settings,
                run_label=f"fresh-{repeat}",
                docker_enabled=docker_enabled,
            )
            result = compare_fresh_replay(
                case_dir,
                fresh_run,
                output_dir=evidence_root / "fresh-reports" / scenario.name / f"r{repeat}",
            )
            fresh_passes += int(result.passed)
            fresh_action_matches += int(result.report["checks"]["action_sequence_match"]["passed"])
            fresh_diff_matches += int(result.report["checks"]["diff_match"]["passed"])
            fresh_reports.append(str(result.report_path))
            fresh_rows.append(
                {
                    "scenario": scenario.name,
                    "repeat": repeat,
                    "passed": result.passed,
                    "action_match": result.report["checks"]["action_sequence_match"]["passed"],
                    "diff_match": result.report["checks"]["diff_match"]["passed"],
                    "report": str(result.report_path),
                    "fresh_run_dir": result.report.get("fresh_run_dir"),
                }
            )

        scenario_tamper_passes = 0
        for tamper_kind in ("trace", "response", "sequence", "workspace"):
            tamper_dir = evidence_root / "tamper" / scenario.name / tamper_kind
            shutil.copytree(case_dir, tamper_dir)
            _tamper(tamper_dir, tamper_kind)
            result = run_deterministic_replay(tamper_dir, output_dir=tamper_dir)
            detected = not result.passed
            scenario_tamper_passes += int(detected)
            tamper_rows.append(
                {
                    "scenario": scenario.name,
                    "tamper_kind": tamper_kind,
                    "detected": detected,
                    "report": str(result.report_path),
                }
            )

        source_state = json.loads((source_run / "state.json").read_text(encoding="utf-8"))
        source_trace = json.loads(
            "[" + ",".join((source_run / "trace.jsonl").read_text(encoding="utf-8").splitlines()) + "]"
        )
        scenario_rows.append(
            {
                "scenario": scenario.name,
                "title": scenario.title,
                "eval_case": str(scenario.case_dir / "case.yaml"),
                "source_run_id": source_state.get("run_id"),
                "source_run_dir": str(source_run),
                "replay_case": str(case_dir),
                "trace_events": len(source_trace),
                "model_responses": sum(1 for event in source_trace if event.get("event") == "model_response"),
                "tool_results": sum(1 for event in source_trace if event.get("event") in {"tool/result", "sandbox_exec_finished"}),
                "artifact_bytes": int((source_state.get("metrics") or {}).get("artifact_bytes", 0)),
                "deterministic_passes": deterministic_passes,
                "deterministic_total": args.deterministic_repeat,
                "fresh_passes": fresh_passes,
                "fresh_total": args.fresh_repeat,
                "fresh_action_matches": fresh_action_matches,
                "fresh_diff_matches": fresh_diff_matches,
                "tamper_detected": scenario_tamper_passes,
                "tamper_total": 4,
                "deterministic_reports": deterministic_case_reports,
                "fresh_reports": fresh_reports,
            }
        )

    total_det = len(deterministic_rows)
    total_fresh = len(fresh_rows)
    total_tamper = len(tamper_rows)
    scorecard = {
        "scenario_count": len(scenarios),
        "deterministic_runs": total_det,
        "fresh_runs": total_fresh,
        "tamper_cases": total_tamper,
        "replay_fidelity": _ratio(sum(int(row["passed"]) for row in deterministic_rows), total_det),
        "evidence_completeness": _ratio(
            sum(int(row["evidence_complete"]) for row in deterministic_rows), total_det
        ),
        "fresh_task_success": _ratio(sum(int(row["passed"]) for row in fresh_rows), total_fresh),
        "status_consistency": _ratio(sum(int(row["passed"]) for row in fresh_rows), total_fresh),
        "action_consistency": _ratio(sum(int(row["action_match"]) for row in fresh_rows), total_fresh),
        "diff_consistency": _ratio(sum(int(row["diff_match"]) for row in fresh_rows), total_fresh),
        "tamper_detection": _ratio(sum(int(row["detected"]) for row in tamper_rows), total_tamper),
        "false_accepts": sum(int(not row["detected"]) for row in tamper_rows),
        "deterministic_duration_ms": _duration_stats(durations),
    }
    report = {
        "schema_version": 1,
        "experiment": "replay-showcase-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "execution_mode": "docker-scripted-provider" if docker_enabled else "local-scripted-provider",
        "docker_available": docker_enabled,
        "docker_image": settings.sandbox.image if docker_enabled else None,
        "cases_root": str(args.cases_root.resolve()),
        "scope": "controlled showcase of the real AgentLoop/replay contracts using visible eval cases",
        "configuration": {
            "deterministic_repeat": args.deterministic_repeat,
            "fresh_repeat": args.fresh_repeat,
            "scenarios": [scenario.name for scenario in scenarios],
        },
        "scorecard": scorecard,
        "scenarios": scenario_rows,
        "deterministic_runs": deterministic_rows,
        "fresh_runs": fresh_rows,
        "tamper_cases": tamper_rows,
        "evidence_root": str(evidence_root),
    }
    report_path = output_dir / "replay_showcase_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "replay_showcase_report.md").write_text(_markdown(report), encoding="utf-8")
    print(f"report: {report_path}")
    print(f"scorecard: {json.dumps(scorecard, ensure_ascii=False)}")
    return 0 if _showcase_passed(scorecard) else 1


def _showcase_settings(*, docker_image: str | None = None) -> Settings:
    route = ProviderRoute(
        name="showcase",
        base_url="https://showcase.invalid/v1",
        api_key="showcase",
        model="scripted-showcase",
    )
    return Settings(
        providers={"showcase": route},
        default_provider="showcase",
        sandbox=SandboxSettings(
            image=docker_image or "eclipse-temurin:17-jdk",
            network="none",
        ),
        budget=BudgetSettings(max_bash_actions=12, max_seconds=120, max_turns=8, max_action_timeout_sec=30),
        context=ContextSettings(compaction_strategy="deterministic"),
        policy=PolicySettings(),
        project=ProjectSettings(milestone="replay-showcase-v1"),
        tooling=ToolingSettings(profile="baseline-bash"),
    )


def _docker_available() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _run_scenario(
    scenario: Scenario,
    fixture: Path,
    runs_root: Path,
    settings: Settings,
    *,
    run_label: str,
    docker_enabled: bool,
) -> Path:
    state = RunState.start(
        scenario.goal,
        suite_id="replay-showcase-v1",
        milestone="replay-showcase-v1",
        stage="showcase",
    )
    workspace = prepare_run_workspace(fixture, run_id=state.run_id, runs_root=runs_root)
    state.run_dir = workspace.run_dir
    state.workspace_host_path = workspace.workspace_dir
    state.artifacts_dir = workspace.artifacts_dir
    state.metrics["showcase_scenario"] = scenario.name
    state.metrics["showcase_run_label"] = run_label
    state.metrics["max_action_timeout_sec"] = settings.budget.max_action_timeout_sec
    state.metrics["max_tool_calls_per_step"] = settings.tooling.max_tool_calls_per_step
    state.metrics["completion_verifier_commands"] = [scenario.verifier]
    state.metrics["completion_verifier_timeout_sec"] = 30
    state.constraints.append(f"Showcase verifier: {scenario.verifier}")
    session = SessionManager(runs_root=runs_root)
    artifacts = ArtifactStore(
        workspace.artifacts_dir,
        display_path_prefix=".minicc_artifacts",
        preview_chars=4_000,
    )
    docker_runner: DockerSandboxRunner | None = None
    if docker_enabled:
        docker_runner = DockerSandboxRunner(
            DockerSandboxConfig(
                image=settings.sandbox.image,
                cpus=settings.sandbox.cpus,
                memory=settings.sandbox.memory,
                pids_limit=settings.sandbox.pids_limit,
                network=settings.sandbox.network,
            )
        )
        state.container_name = docker_runner.start(
            run_id=state.run_id,
            workspace_dir=workspace.workspace_dir,
            artifacts_dir=workspace.artifacts_dir,
        )
        executor = DockerCommandExecutor(docker_runner, artifacts=artifacts)
    else:
        executor = LocalCommandExecutor(artifacts=artifacts, preview_chars=4_000)
    provider = ScriptedProvider(scenario.responses)
    verifier = CommandCompletionVerifier(commands=(scenario.verifier,), timeout_sec=30)
    cli._attach_repository_context(state, workspace.workspace_dir)
    session.save(state)
    loop = cli._build_loop(
        provider,
        executor,
        settings=settings,
        session=session,
        state=state,
        completion_verifier=verifier,
    )
    try:
        result = loop.run(state)
        session.save(result.state)
        write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir)
        cli._write_run_artifact_index(result.state)
        write_metrics(result.state)
        write_run_report(result.state)
        trace_path = workspace.run_dir / "trace.jsonl"
        if trace_path.is_file():
            project_trace(trace_path, workspace.run_dir)
        save_run_state(result.state)
        if result.state.status != "completed":
            raise RuntimeError(
                f"showcase scenario {scenario.name} did not complete: {result.state.state_summary}"
            )
        return workspace.run_dir
    finally:
        if docker_runner is not None:
            docker_runner.cleanup(state.container_name)


def _tamper(case_dir: Path, kind: str) -> None:
    if kind == "trace":
        path = case_dir / "trace.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "{\"event\":\"forged\"}\n", encoding="utf-8")
    elif kind == "response":
        path = case_dir / "model_responses.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "{\"response_text\":\"forged\"}\n", encoding="utf-8")
    elif kind == "sequence":
        path = case_dir / "trace.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[0])
        event["sequence"] = 999999
        lines[0] = json.dumps(event, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif kind == "workspace":
        path = case_dir / "workspace" / "README.txt"
        if not path.is_file():
            path = next((item for item in (case_dir / "workspace").rglob("*") if item.is_file()), path)
        path.write_text(path.read_text(encoding="utf-8", errors="replace") + "FORGED\n", encoding="utf-8")


def _remove_tree(path: Path) -> None:
    """Remove showcase evidence even when Git objects are read-only on Windows."""
    shutil.rmtree(path, onerror=_remove_readonly)


def _remove_readonly(function: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _duration_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None}
    ordered = sorted(value * 1000 for value in values)
    return {
        "count": len(ordered),
        "p50_ms": round(ordered[len(ordered) // 2], 2),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
    }


def _showcase_passed(scorecard: dict[str, Any]) -> bool:
    keys = (
        "replay_fidelity",
        "evidence_completeness",
        "fresh_task_success",
        "status_consistency",
        "action_consistency",
        "diff_consistency",
        "tamper_detection",
    )
    return all(scorecard[key]["rate"] == 1.0 for key in keys) and scorecard["false_accepts"] == 0


def _markdown(report: dict[str, Any]) -> str:
    scorecard = report["scorecard"]
    lines = [
        "# miniCC 评测与重放 Showcase",
        "",
        "> 本报告是受控本地 showcase：使用真实 AgentLoop、workspace、verifier、trace 和 replay 实现，模型响应由 scripted provider 固定，以隔离网络与模型随机性。它验证 Harness 合同，不代表通用 Coding Agent benchmark。",
        "",
        "## 实验配置",
        "",
        f"- 场景数：`{scorecard['scenario_count']}`",
        f"- Deterministic replay：`{scorecard['deterministic_runs']}` 次",
        f"- Fresh replay：`{scorecard['fresh_runs']}` 次",
        f"- Tamper cases：`{scorecard['tamper_cases']}` 个",
        f"- 执行模式：`{report['execution_mode']}`",
        f"- Cases root：`{report['cases_root']}`",
        *([f"- Docker image：`{report['docker_image']}`"] if report.get("docker_image") else []),
        "",
        "## Scorecard",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
    ]
    labels = {
        "replay_fidelity": "Replay Fidelity",
        "evidence_completeness": "Evidence Completeness",
        "fresh_task_success": "Fresh Task Success",
        "status_consistency": "Status Consistency",
        "action_consistency": "Action Consistency",
        "diff_consistency": "Diff Consistency",
        "tamper_detection": "Tamper Detection",
    }
    for key, label in labels.items():
        value = scorecard[key]
        lines.append(f"| {label} | `{value['numerator']}/{value['denominator']} ({value['rate']:.0%})` |")
    duration = scorecard["deterministic_duration_ms"]
    lines.extend(
        [
            f"| Deterministic p50 | `{duration['p50_ms']} ms` |",
            f"| Deterministic p95 | `{duration['p95_ms']} ms` |",
            "",
            "## 场景结果",
            "",
            "| Case | Eval case | Trace events | Model responses | Artifacts | Deterministic | Fresh | Tamper |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["scenarios"]:
        lines.append(
            f"| `{row['scenario']}` | `{Path(row['eval_case']).as_posix()}` | {row['trace_events']} | {row['model_responses']} | {row['artifact_bytes']} B | "
            f"{row['deterministic_passes']}/{row['deterministic_total']} | "
            f"{row['fresh_passes']}/{row['fresh_total']} | "
            f"{row['tamper_detected']}/{row['tamper_total']} |"
        )
    lines.extend(
        [
            "",
            "## 面试结论",
            "",
            "这组实验验证了三条边界：",
            "",
            "1. deterministic replay 能够在无模型、无工具副作用条件下重新解析响应并验证原始执行证据。",
            "2. fresh replay 能从 baseline workspace 重新启动当前 Runtime，并由 verifier 裁决任务结果。",
            "3. trace、model response、workspace 和 sequence 任一证据被篡改后，replay 会拒绝该 case。",
            "",
            f"证据根目录：`{report['evidence_root']}`",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
