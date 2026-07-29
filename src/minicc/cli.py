from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from minicc import __version__
from minicc.config import BudgetSettings, PolicySettings, SandboxSettings, Settings, load_settings
from minicc.core.checkpoint import CheckpointError, CheckpointManager
from minicc.core.context import STABLE_PREFIX, ContextBuilder, ContextConfig
from minicc.core.ledger import apply_cleanup_plan, build_cleanup_plan, write_artifact_index
from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig
from minicc.core.provider import CompletionOptions, OpenAICompatibleProvider
from minicc.core.run_catalog import RunCatalog, index_acceptance_history
from minicc.core.session import SessionManager
from minicc.core.state import RunState, state_path_for_run
from minicc.evals.case import EvalCase, discover_cases
from minicc.evals.compaction_ab import (
    build_compaction_ab_report,
    load_suite_report as load_compaction_suite_report,
    write_compaction_ab_report,
)
from minicc.evals.cache_ab import (
    build_cache_ab_report,
    load_suite_report as load_cache_suite_report,
    write_cache_ab_report,
)
from minicc.evals.cache_probe import load_cache_probe_report
from minicc.evals.cache_probe_runner import (
    CacheProbeRunConfig,
    cache_sequence_namespace,
    run_fixed_cache_probe,
)
from minicc.evals.runner import run_eval_suite, write_eval_report, write_suite_report
from minicc.memory.feedback import FeedbackMemory
from minicc.memory.compaction import SemanticCompactor
from minicc.policy.factory import build_policy_chain
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.docker_runner import DockerCommandExecutor, DockerSandboxConfig, DockerSandboxRunner
from minicc.sandbox.local_runner import LocalCommandExecutor
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff
from minicc.skills.registry import SkillRegistry
from minicc.trace.recorder import TraceRecorder, trace_path_for


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicc",
        description="Bash-first CodeAct Agent Harness.",
    )
    parser.add_argument("--version", action="version", version=f"minicc {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run a goal through the miniCC agent loop.")
    run_parser.add_argument("goal", help="User goal for the agent.")
    run_parser.add_argument("--milestone", default=None, help="Override project.milestone for run indexing.")
    run_parser.add_argument("--max-turns", type=int, default=None, help="Override budget.max_turns.")
    run_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Execute bash actions on the host for development demos instead of Docker.",
    )
    run_parser.add_argument(
        "--no-workspace-copy",
        action="store_true",
        help="Use the current directory directly. Intended only with --execute-local.",
    )
    run_parser.add_argument(
        "--docker-image",
        default=None,
        help="Override sandbox.image.",
    )
    run_parser.add_argument(
        "--stream",
        action="store_true",
        default=None,
        help="Override provider.stream to true.",
    )
    run_parser.set_defaults(handler=run_command)

    resume_parser = subparsers.add_parser("resume", help="Resume a waiting run.")
    resume_parser.add_argument("run_id", help="Run id to resume.")
    resume_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Resume using local execution instead of Docker.",
    )
    resume_parser.set_defaults(handler=resume_command)

    approve_parser = subparsers.add_parser("approve", help="Approve a pending action for a run.")
    approve_parser.add_argument("run_id", help="Run id waiting for approval.")
    approve_parser.add_argument("--yes", action="store_true", help="Approve without interactive prompt.")
    approve_parser.set_defaults(handler=approve_command)

    deny_parser = subparsers.add_parser("deny", help="Deny a pending action for a run.")
    deny_parser.add_argument("run_id", help="Run id waiting for approval.")
    deny_parser.add_argument("--reason", default="User denied the action.", help="Reason returned to the model.")
    deny_parser.set_defaults(handler=deny_command)

    eval_parser = subparsers.add_parser("eval", help="Run eval cases and write JSON/Markdown reports.")
    eval_parser.add_argument("path", nargs="?", default="eval_cases", help="Eval cases directory.")
    eval_parser.add_argument("--milestone", default=None, help="Override project.milestone for run indexing.")
    eval_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Run eval bash actions locally instead of Docker.",
    )
    run_parser.add_argument(
        "--interrupt-after-steps",
        type=int,
        default=None,
        help="Create a controlled checkpoint interruption after N recorded trajectory steps.",
    )
    resume_parser.add_argument(
        "--from-checkpoint",
        action="store_true",
        help="Restore the latest validated checkpoint instead of resuming an approval.",
    )
    eval_parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run every selected eval case N times while preserving each run directory.",
    )
    eval_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write aggregate JSON/Markdown reports to this directory.",
    )
    eval_parser.add_argument(
        "--case",
        dest="case_names",
        action="append",
        default=None,
        help="Run only the named case. Repeat this option to select multiple cases.",
    )
    eval_parser.add_argument(
        "--release-gate",
        action="store_true",
        help="Require a clean immutable Git commit, Docker execution, and repeat >= 3.",
    )
    eval_parser.add_argument(
        "--context-variant",
        choices=("a0", "a1"),
        default=None,
        help="V2.1 experiment variant: a0=uncompressed baseline, a1=semantic compaction.",
    )
    eval_parser.add_argument(
        "--cache-variant",
        choices=("p0", "p1"),
        default=None,
        help="V2.1.1 experiment variant: p0=rebuild layout, p1=append-only layout.",
    )
    eval_parser.add_argument(
        "--cache-sequence-id",
        default=None,
        help="Within-round cache experiment namespace shared by P0/P1, for example round-1.",
    )
    eval_parser.add_argument(
        "--execution-order",
        choices=("p0-first", "p1-first"),
        default=None,
        help="Record the balanced P0/P1 order used for this cache experiment round.",
    )
    eval_parser.set_defaults(handler=eval_command)

    compaction_parser = subparsers.add_parser(
        "compaction-report",
        help="Compare paired V2.1 A0/A1 suite reports.",
    )
    compaction_parser.add_argument(
        "--a0",
        action="append",
        type=Path,
        required=True,
        help="A0 suite report.json; repeat once per independent round.",
    )
    compaction_parser.add_argument(
        "--a1",
        action="append",
        type=Path,
        required=True,
        help="A1 suite report.json; repeat once per independent round.",
    )
    compaction_parser.add_argument("--output-dir", type=Path, required=True)
    compaction_parser.set_defaults(handler=compaction_report_command)

    cache_probe_parser = subparsers.add_parser(
        "cache-probe",
        help="Run an immutable V2.1.1 fixed-sequence Prompt Cache probe.",
    )
    cache_probe_parser.add_argument(
        "--cache-variant",
        choices=("p0", "p1"),
        required=True,
        help="p0=rebuild layout, p1=append-only layout.",
    )
    cache_probe_parser.add_argument(
        "--repeat",
        type=int,
        default=5,
        help="Number of growing fixed-sequence requests (formal minimum: 5).",
    )
    cache_probe_parser.add_argument(
        "--milestone",
        default=None,
        help="Override project.milestone recorded in probe evidence.",
    )
    cache_probe_parser.add_argument(
        "--execution-order",
        choices=("p0-first", "p1-first"),
        default=None,
        help="Record the balanced variant order used for this independent round.",
    )
    cache_probe_parser.add_argument(
        "--cache-sequence-id",
        required=True,
        help="Within-round namespace shared by P0/P1, for example round-1.",
    )
    cache_probe_parser.add_argument(
        "--release-gate",
        action="store_true",
        help="Require a clean immutable Git commit and repeat >= 5.",
    )
    cache_probe_parser.set_defaults(handler=cache_probe_command)

    cache_report_parser = subparsers.add_parser(
        "cache-report",
        help="Compare exactly two rounds of V2.1.1 fixed-probe and real-case evidence.",
    )
    cache_report_parser.add_argument(
        "--p0-probe",
        action="append",
        type=Path,
        required=True,
        help="P0 fixed probe report.json; repeat once per round.",
    )
    cache_report_parser.add_argument(
        "--p1-probe",
        action="append",
        type=Path,
        required=True,
        help="P1 fixed probe report.json; repeat once per round.",
    )
    cache_report_parser.add_argument(
        "--p0-eval",
        action="append",
        type=Path,
        required=True,
        help="P0 real-case suite report.json; repeat once per round.",
    )
    cache_report_parser.add_argument(
        "--p1-eval",
        action="append",
        type=Path,
        required=True,
        help="P1 real-case suite report.json; repeat once per round.",
    )
    cache_report_parser.add_argument("--output-dir", type=Path, required=True)
    cache_report_parser.set_defaults(handler=cache_report_command)

    traces_parser = subparsers.add_parser("traces", help="List runs that have trace or metrics files.")
    traces_parser.set_defaults(handler=traces_command)

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="List unreferenced old runs; delete only when --apply is supplied.",
    )
    cleanup_parser.add_argument(
        "--older-than-hours",
        type=float,
        default=168.0,
        help="Select unreferenced runs older than this many hours (default: 168).",
    )
    cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete exactly the runs selected by the displayed cleanup plan.",
    )
    cleanup_parser.set_defaults(handler=cleanup_command)

    web_parser = subparsers.add_parser("web", help="Serve a read-only trace viewer.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    web_parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    web_parser.set_defaults(handler=web_command)
    return parser


def run_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    milestone = _effective_milestone(settings, args)
    catalog = RunCatalog(Path.cwd() / ".minicc" / "versions")
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    workspace = None
    runner = None
    state = RunState.start(args.goal, milestone=milestone, stage="daily_development")
    result = None
    try:
        if args.no_workspace_copy:
            if not args.execute_local:
                print("--no-workspace-copy requires --execute-local.", file=sys.stderr)
                return 2
            state.workspace_host_path = Path.cwd()
            executor = LocalCommandExecutor()
        else:
            workspace = prepare_run_workspace(
                Path.cwd(),
                run_id=state.run_id,
                ignored_allowlist=settings.workspace.ignored_allowlist,
                allowlist_source="minicc.yaml:workspace.ignored_allowlist",
            )
            state.run_dir = workspace.run_dir
            state.workspace_host_path = workspace.workspace_dir
            state.artifacts_dir = workspace.artifacts_dir
            artifacts = ArtifactStore(
                workspace.artifacts_dir,
                display_path_prefix=".minicc_artifacts",
                preview_chars=settings.context.artifact_preview_chars,
            )

            if args.execute_local:
                executor = LocalCommandExecutor(
                    artifacts=artifacts,
                    preview_chars=settings.context.artifact_preview_chars,
                )
            else:
                runner = DockerSandboxRunner(
                    DockerSandboxConfig(
                        image=args.docker_image or settings.sandbox.image,
                        cpus=settings.sandbox.cpus,
                        memory=settings.sandbox.memory,
                        pids_limit=settings.sandbox.pids_limit,
                        network=settings.sandbox.network,
                    )
                )
                state.container_name = runner.start(
                    run_id=state.run_id,
                    workspace_dir=workspace.workspace_dir,
                    artifacts_dir=workspace.artifacts_dir,
                )
                executor = DockerCommandExecutor(runner, artifacts=artifacts)

        session = SessionManager()
        session.save(state)
        loop = _build_loop(
            provider,
            executor,
            settings=settings,
            session=session,
            state=state,
            max_turns=settings.budget.max_turns if args.max_turns is None else args.max_turns,
            stream=settings.provider.stream if args.stream is None else args.stream,
            interrupt_after_steps=getattr(args, "interrupt_after_steps", None),
        )
        result = loop.run(state)
        session.save(result.state)
    except FileNotFoundError as exc:
        print(f"Required executable was not found: {exc.filename}", file=sys.stderr)
        return 127
    except Exception as exc:
        state.status = "failed"
        state.state_summary = f"Run setup or execution failed: {exc}"
        print(state.state_summary, file=sys.stderr)
        return 1
    finally:
        if runner is not None:
            runner.cleanup(state.container_name)
        if workspace is not None:
            try:
                write_workspace_diff(workspace.workspace_dir, workspace.artifacts_dir)
                _write_run_artifact_index(state)
            except Exception as exc:
                print(f"Failed to finalize run evidence: {exc}", file=sys.stderr)
        catalog.register_state(milestone, state, git_commit=_git_evidence(Path.cwd())[0])

    if result is None:
        return 1
    _print_run_result(result.state, workspace.run_dir if workspace is not None else None)
    if result.state.status == "failed":
        return 1
    return 0


def resume_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    catalog = RunCatalog(Path.cwd() / ".minicc" / "versions")
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    state_path = state_path_for_run(args.run_id)
    if not state_path.exists():
        print(f"Run state not found: {state_path}", file=sys.stderr)
        return 2

    session = SessionManager()
    state = session.load(args.run_id)
    trace = TraceRecorder(trace_path_for(state))
    from_checkpoint = bool(getattr(args, "from_checkpoint", False))
    restored_trajectory = None
    if from_checkpoint:
        try:
            restored = CheckpointManager(state_path.parent, trace=trace).restore_latest(args.run_id)
        except CheckpointError as exc:
            print(f"Cannot resume checkpoint: {exc}", file=sys.stderr)
            return 2
        state = restored.state
        restored_trajectory = restored.trajectory
    elif state.status != "waiting_approval":
        print(f"Run {args.run_id} is not waiting for approval. Current status: {state.status}", file=sys.stderr)
        return 2

    runner = None
    try:
        artifacts_dir = state.artifacts_dir or state_path.parent / "artifacts"
        artifacts = ArtifactStore(
            artifacts_dir,
            display_path_prefix=".minicc_artifacts",
            preview_chars=settings.context.artifact_preview_chars,
        )
        if args.execute_local:
            executor = LocalCommandExecutor(
                artifacts=artifacts,
                preview_chars=settings.context.artifact_preview_chars,
            )
        else:
            if state.workspace_host_path is None:
                print("Cannot resume: missing workspace_host_path in state.", file=sys.stderr)
                return 2
            runner = DockerSandboxRunner(
                DockerSandboxConfig(
                    image=settings.sandbox.image,
                    cpus=settings.sandbox.cpus,
                    memory=settings.sandbox.memory,
                    pids_limit=settings.sandbox.pids_limit,
                    network=settings.sandbox.network,
                )
            )
            state.container_name = runner.start(
                run_id=state.run_id,
                workspace_dir=state.workspace_host_path,
                artifacts_dir=artifacts_dir,
            )
            executor = DockerCommandExecutor(runner, artifacts=artifacts)

        if not from_checkpoint:
            session.apply_pending_approval_result(state, executor, trace=trace)
        if state.status == "running":
            loop = _build_loop(provider, executor, settings=settings, session=session, state=state)
            result = loop.run(state, restored_trajectory) if from_checkpoint else loop.run(state)
        else:
            result = type("ResumeResult", (), {"state": state})()
        session.save(result.state)
    except Exception as exc:
        state.status = "failed"
        state.state_summary = f"Resume failed: {exc}"
        session.save(state)
        print(state.state_summary, file=sys.stderr)
        return 1
    finally:
        if runner is not None:
            runner.cleanup(state.container_name)
        if state.workspace_host_path is not None and state.artifacts_dir is not None:
            try:
                write_workspace_diff(state.workspace_host_path, state.artifacts_dir)
                _write_run_artifact_index(state)
            except Exception as exc:
                print(f"Failed to finalize run evidence: {exc}", file=sys.stderr)
        catalog.update_existing_state(state, fallback_milestone=settings.project.milestone)

    _print_run_result(result.state, result.state.run_dir)
    return 1 if result.state.status == "failed" else 0


def approve_command(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Use --yes to approve the pending action explicitly.", file=sys.stderr)
        return 2
    session = SessionManager()
    state = _load_waiting_state(session, args.run_id, require_pending_action=True)
    if state is None:
        return 2
    session.approve(state)
    print(f"Approved pending action for run {args.run_id}. Use `uv run minicc resume {args.run_id}` to continue.")
    return 0


def deny_command(args: argparse.Namespace) -> int:
    session = SessionManager()
    state = _load_waiting_state(session, args.run_id, require_pending_action=False)
    if state is None:
        return 2
    session.deny(state, args.reason)
    print(f"Denied pending action for run {args.run_id}. Use `uv run minicc resume {args.run_id}` to continue.")
    return 0


def _print_run_result(state: RunState, run_dir: Path | None) -> None:
    print(f"run_id: {state.run_id}")
    print(f"status: {state.status}")
    print(f"turns: {state.metrics['turns']}")
    print(f"bash_actions: {state.metrics['bash_actions']}")
    print(f"policy_denials: {state.metrics.get('policy_denials', 0)}")
    print(f"approvals_requested: {state.metrics.get('approvals_requested', 0)}")
    print(f"artifact_bytes: {state.metrics.get('artifact_bytes', 0)}")
    if run_dir is not None:
        print(f"run_dir: {run_dir}")
    if state.workspace_host_path is not None:
        print(f"workspace: {state.workspace_host_path}")
    if state.artifacts_dir is not None:
        print(f"artifacts: {state.artifacts_dir}")
    if state.open_questions:
        print("question: " + state.open_questions[-1])
    if state.final_answer:
        print("\n" + state.final_answer)
    if state.status == "failed" and state.state_summary:
        print(state.state_summary, file=sys.stderr)


def _build_provider_or_print_error(settings: Settings) -> OpenAICompatibleProvider | None:
    missing = [
        name
        for name, value in {
            "MINICC_BASE_URL": settings.base_url,
            "MINICC_API_KEY": settings.api_key,
            "MINICC_MODEL": settings.model,
        }.items()
        if not value
    ]
    if missing:
        print(
            "Missing provider configuration: "
            + ", ".join(missing)
            + "\nSet these values in .env / environment variables and minicc.yaml.",
            file=sys.stderr,
        )
        return None

    return OpenAICompatibleProvider(
        base_url=settings.base_url or "",
        api_key=settings.api_key or "",
        model=settings.model or "",
        timeout_sec=settings.provider.timeout_sec,
        max_retries=settings.provider.max_retries,
    )


def _build_loop(
    provider: OpenAICompatibleProvider,
    executor: BashExecutor,
    *,
    settings: Settings,
    session: SessionManager | None = None,
    state: RunState | None = None,
    max_turns: int | None = None,
    stream: bool | None = None,
    interrupt_after_steps: int | None = None,
) -> AgentLoop:
    skill_root = (state.workspace_host_path if state and state.workspace_host_path else Path.cwd()) / "skills"
    trace = TraceRecorder(trace_path_for(state)) if state is not None else TraceRecorder()
    checkpoint_manager = (
        CheckpointManager(state.run_dir, trace=trace)
        if state is not None and state.run_dir is not None and state.workspace_host_path is not None
        else None
    )
    semantic_compactor = None
    if settings.context.compaction_strategy == "semantic":
        semantic_compactor = SemanticCompactor(
            provider,
            trace=trace,
            max_input_chars=settings.context.semantic_max_input_chars,
            max_summary_chars=settings.context.summary_max_chars,
            max_completion_tokens=settings.context.semantic_max_completion_tokens,
        )
    prompt_layout = settings.context.prompt_layout
    if state is not None and int(state.metrics.get("turns") or 0) > 0:
        stored_layout = state.metrics.get("prompt_layout")
        prompt_layout = stored_layout if stored_layout in {"rebuild", "append"} else "rebuild"
    feedback_memory = (
        None
        if state is not None
        and state.prompt_namespace.startswith("cache-experiment/")
        else FeedbackMemory(Path.cwd() / ".minicc" / "memory" / "feedback_rules.jsonl")
    )
    return AgentLoop(
        provider,
        executor,
        context_builder=ContextBuilder(
            ContextConfig(
                max_prompt_chars=settings.context.max_prompt_chars,
                recent_turns=settings.context.recent_turns,
                artifact_preview_chars=settings.context.artifact_preview_chars,
                summary_max_chars=settings.context.summary_max_chars,
                field_preview_chars=settings.context.field_preview_chars,
                compaction_strategy=settings.context.compaction_strategy,
                retention_markers=settings.context.retention_markers,
                prompt_layout=prompt_layout,
            ),
            skill_registry=SkillRegistry(skill_root),
            feedback_memory=feedback_memory,
            semantic_compactor=semantic_compactor,
        ),
        policy_chain=build_policy_chain(settings),
        session=session,
        trace=trace,
        checkpoint_manager=checkpoint_manager,
        config=LoopConfig(
            max_turns=settings.budget.max_turns if max_turns is None else max_turns,
            max_action_timeout_sec=settings.budget.max_action_timeout_sec,
            model_options=CompletionOptions(
                temperature=settings.temperature,
                stream=settings.provider.stream if stream is None else stream,
                include_usage=settings.provider.include_usage,
                json_mode=settings.provider.json_mode,
                max_tokens=settings.provider.max_completion_tokens,
            ),
            interrupt_after_steps=interrupt_after_steps,
        ),
    )


def _load_waiting_state(
    session: SessionManager,
    run_id: str,
    *,
    require_pending_action: bool,
) -> RunState | None:
    state_path = session.state_path(run_id)
    if not state_path.exists():
        print(f"Run state not found: {state_path}", file=sys.stderr)
        return None
    state = session.load(run_id)
    if state.status != "waiting_approval":
        print(f"Run {run_id} is not waiting for approval. Current status: {state.status}", file=sys.stderr)
        return None
    if require_pending_action and state.pending_action is None:
        print(f"Run {run_id} has no pending bash action to approve or deny.", file=sys.stderr)
        return None
    return state


def eval_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    context_variant = getattr(args, "context_variant", None)
    if context_variant is not None:
        strategy = "semantic" if context_variant == "a1" else "disabled"
        settings = replace(settings, context=replace(settings.context, compaction_strategy=strategy))
    cache_variant = getattr(args, "cache_variant", None)
    cache_sequence_id = str(getattr(args, "cache_sequence_id", "") or "").strip()
    execution_order = getattr(args, "execution_order", None)
    if cache_variant is not None and not cache_sequence_id:
        print("--cache-variant requires --cache-sequence-id.", file=sys.stderr)
        return 2
    if cache_variant is not None and not execution_order:
        print("--cache-variant requires --execution-order.", file=sys.stderr)
        return 2
    if cache_sequence_id and cache_variant is None:
        print("--cache-sequence-id requires --cache-variant.", file=sys.stderr)
        return 2
    if execution_order and cache_variant is None:
        print("--execution-order requires --cache-variant.", file=sys.stderr)
        return 2
    cache_namespace = ""
    if cache_sequence_id:
        try:
            cache_namespace = cache_sequence_namespace(cache_sequence_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if cache_variant is not None:
        prompt_layout = "append" if cache_variant == "p1" else "rebuild"
        settings = replace(settings, context=replace(settings.context, prompt_layout=prompt_layout))
    milestone = _effective_milestone(settings, args)
    catalog = RunCatalog(Path.cwd() / ".minicc" / "versions")
    git_commit, worktree_dirty = _git_evidence(Path.cwd())
    if args.release_gate:
        gate_error = _release_gate_error(args, git_commit, worktree_dirty, settings.sandbox.image)
        if gate_error:
            print(f"Release gate rejected: {gate_error}", file=sys.stderr)
            return 2
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    runs_root = Path.cwd() / ".minicc" / "runs"
    suites_root = Path.cwd() / ".minicc" / "suites"
    suite_stage = "formal_acceptance" if args.release_gate else "development_precheck"

    def agent_runner(case: EvalCase, state: RunState) -> RunState:
        state.constraints.extend(_case_constraints(case))
        if cache_namespace:
            state.prompt_namespace = cache_namespace
        artifacts = ArtifactStore(
            state.artifacts_dir or state.run_dir / "artifacts",
            display_path_prefix=".minicc_artifacts",
            preview_chars=settings.context.artifact_preview_chars,
        )
        runner = None
        try:
            if args.execute_local or case.sandbox_mode == "local":
                executor = LocalCommandExecutor(
                    artifacts=artifacts,
                    preview_chars=settings.context.artifact_preview_chars,
                )
            else:
                runner = DockerSandboxRunner(
                    DockerSandboxConfig(
                        image=settings.sandbox.image,
                        cpus=settings.sandbox.cpus,
                        memory=settings.sandbox.memory,
                        pids_limit=settings.sandbox.pids_limit,
                        network="bridge" if case.sandbox_mode == "dev" else settings.sandbox.network,
                    )
                )
                state.container_name = runner.start(
                    run_id=state.run_id,
                    workspace_dir=state.workspace_host_path,
                    artifacts_dir=state.artifacts_dir,
                    writable_paths=case.writable_paths,
                )
                executor = DockerCommandExecutor(runner, artifacts=artifacts)
            session = SessionManager()
            session.save(state)
            case_settings = _settings_for_eval_case(settings, case)
            loop = _build_loop(
                provider,
                executor,
                settings=case_settings,
                session=session,
                state=state,
                max_turns=_case_int(case, "max_turns", case_settings.budget.max_turns),
            )
            return loop.run(state).state
        finally:
            if runner is not None:
                runner.cleanup(state.container_name)

    selected_case_names = set(args.case_names or [])
    case_contexts = {
        case.name: dict(case.context)
        for case in discover_cases(Path(args.path))
        if not selected_case_names or case.name in selected_case_names
    }
    configuration = {
        "base_url": settings.base_url or "",
        "model": settings.model or "",
        "temperature": settings.temperature,
        "stream": settings.provider.stream,
        "include_usage": settings.provider.include_usage,
        "sandbox_mode": settings.sandbox.mode,
        "execute_local": bool(args.execute_local),
        "json_mode": settings.provider.json_mode,
        "max_completion_tokens": settings.provider.max_completion_tokens,
        "provider_max_retries": settings.provider.max_retries,
        "provider_timeout_sec": settings.provider.timeout_sec,
        "cache_scope_sha256": _secret_fingerprint(settings.api_key),
        "docker_image": settings.sandbox.image,
        "git_commit": git_commit,
        "worktree_dirty": worktree_dirty,
        "release_gate": bool(args.release_gate),
        "milestone": milestone,
        "context_variant": context_variant or "configured",
        "cache_variant": cache_variant or "configured",
        "cache_sequence_id": cache_sequence_id or None,
        "execution_order": execution_order,
        "feedback_memory_mode": "disabled" if cache_variant else "configured",
        "prompt_layout": settings.context.prompt_layout,
        "compaction_strategy": settings.context.compaction_strategy,
        "system_prefix_sha256": hashlib.sha256(STABLE_PREFIX.encode("utf-8")).hexdigest(),
        "max_prompt_chars": settings.context.max_prompt_chars,
        "recent_turns": settings.context.recent_turns,
        "semantic_max_completion_tokens": settings.context.semantic_max_completion_tokens,
        "case_contexts": case_contexts,
    }

    result = run_eval_suite(
        Path(args.path),
        runs_root=runs_root,
        agent_runner=agent_runner,
        repeat=args.repeat,
        configuration=configuration,
        preserve_runs=True,
        case_names=args.case_names,
        milestone=milestone,
        stage=suite_stage,
    )
    bundle = write_suite_report(result, suites_root)
    json_path = bundle.report_json_path
    markdown_path = bundle.report_markdown_path
    if args.output_dir is not None:
        export_dir = args.output_dir / result.suite_id
        json_path, markdown_path = write_eval_report(result, export_dir)
    catalog_entries = []
    for case_result in result.cases:
        catalog_entries.append(
            catalog.register_eval_result(
                milestone,
                case_result,
                stage=suite_stage,
                git_commit=git_commit,
                report_path=str(bundle.report_json_path),
                suite_path=str(bundle.manifest_path),
            )
        )
    ledger_complete = all(entry is not None for entry in catalog_entries)
    print(f"eval_status: {'PASS' if result.passed else 'FAIL'}")
    print(f"ledger_status: {'COMPLETE' if ledger_complete else 'INCOMPLETE'}")
    print(f"suite_id: {result.suite_id}")
    print(f"suite_manifest: {bundle.manifest_path}")
    print(f"json_report: {json_path}")
    print(f"markdown_report: {markdown_path}")
    return 0 if result.passed and ledger_complete else 1


def compaction_report_command(args: argparse.Namespace) -> int:
    if len(args.a0) != len(args.a1):
        print("compaction-report requires the same number of --a0 and --a1 reports.", file=sys.stderr)
        return 2
    try:
        pairs = [
            (
                load_compaction_suite_report(a0_path),
                load_compaction_suite_report(a1_path),
            )
            for a0_path, a1_path in zip(args.a0, args.a1, strict=True)
        ]
        report = build_compaction_ab_report(pairs)
        bundle = write_compaction_ab_report(report, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"Cannot build compaction report: {exc}", file=sys.stderr)
        return 2
    print(f"compaction_ab_status: {report['status']}")
    print(f"json_report: {bundle.json_path}")
    print(f"markdown_report: {bundle.markdown_path}")
    return 0 if report["passed"] else 1


def cache_probe_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    milestone = _effective_milestone(settings, args)
    git_commit, worktree_dirty = _git_evidence(Path.cwd())
    if args.release_gate:
        gate_error = _cache_probe_release_gate_error(args, git_commit, worktree_dirty)
        if gate_error:
            print(f"Cache probe release gate rejected: {gate_error}", file=sys.stderr)
            return 2
    if args.repeat < 1:
        print("--repeat must be at least 1.", file=sys.stderr)
        return 2
    if settings.context.compaction_strategy == "semantic":
        print(
            "cache-probe requires deterministic or disabled compaction configuration.",
            file=sys.stderr,
        )
        return 2

    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2
    prompt_layout = "append" if args.cache_variant == "p1" else "rebuild"
    context = ContextConfig(
        max_prompt_chars=settings.context.max_prompt_chars,
        recent_turns=settings.context.recent_turns,
        artifact_preview_chars=settings.context.artifact_preview_chars,
        summary_max_chars=settings.context.summary_max_chars,
        field_preview_chars=settings.context.field_preview_chars,
        compaction_strategy=settings.context.compaction_strategy,
        retention_markers=settings.context.retention_markers,
        prompt_layout=prompt_layout,
    )
    configuration = {
        "base_url": settings.base_url or "",
        "model": settings.model or "",
        "temperature": settings.temperature,
        "stream": settings.provider.stream,
        "include_usage": settings.provider.include_usage,
        "json_mode": settings.provider.json_mode,
        "max_completion_tokens": settings.provider.max_completion_tokens,
        "provider_max_retries": settings.provider.max_retries,
        "provider_timeout_sec": settings.provider.timeout_sec,
        "cache_scope_sha256": _secret_fingerprint(settings.api_key),
        "git_commit": git_commit,
        "worktree_dirty": worktree_dirty,
        "release_gate": bool(args.release_gate),
        "milestone": milestone,
        "compaction_strategy": settings.context.compaction_strategy,
        "recent_turns": settings.context.recent_turns,
        "max_prompt_chars": settings.context.max_prompt_chars,
        "execution_order": args.execution_order,
        "feedback_memory_mode": "disabled",
    }
    try:
        bundle = run_fixed_cache_probe(
            provider,
            probes_root=Path.cwd() / ".minicc" / "cache-probes",
            config=CacheProbeRunConfig(
                variant=args.cache_variant,
                repeat=args.repeat,
                cache_sequence_id=args.cache_sequence_id,
                context=context,
                model_options=CompletionOptions(
                    temperature=settings.temperature,
                    stream=settings.provider.stream,
                    include_usage=settings.provider.include_usage,
                    json_mode=settings.provider.json_mode,
                    max_tokens=settings.provider.max_completion_tokens,
                ),
                configuration=configuration,
                milestone=milestone,
                stage="formal_acceptance" if args.release_gate else "development_precheck",
            ),
        )
        report = load_cache_probe_report(
            bundle.report_json_path,
            verify_manifest=True,
        )
    except (OSError, ValueError) as exc:
        print(f"Cannot run cache probe: {exc}", file=sys.stderr)
        return 2

    print(f"cache_probe_status: {report['result']}")
    print(f"cache_probe_id: {bundle.probe_id}")
    print(f"cache_state: {report['cache']['cache_state']}")
    print(f"weighted_hit_rate: {report['cache']['weighted_hit_rate']}")
    print(f"json_report: {bundle.report_json_path}")
    print(f"markdown_report: {bundle.report_markdown_path}")
    return 0 if report["passed"] else 1


def cache_report_command(args: argparse.Namespace) -> int:
    lengths = {
        len(args.p0_probe),
        len(args.p1_probe),
        len(args.p0_eval),
        len(args.p1_eval),
    }
    if len(lengths) != 1:
        print(
            "cache-report requires equal numbers of --p0-probe, --p1-probe, "
            "--p0-eval, and --p1-eval reports.",
            file=sys.stderr,
        )
        return 2
    if lengths != {2}:
        print("cache-report requires exactly two independent rounds.", file=sys.stderr)
        return 2
    try:
        rounds = [
            (
                load_cache_probe_report(p0_probe, verify_manifest=True),
                load_cache_probe_report(p1_probe, verify_manifest=True),
                load_cache_suite_report(p0_eval, verify_manifest=True),
                load_cache_suite_report(p1_eval, verify_manifest=True),
            )
            for p0_probe, p1_probe, p0_eval, p1_eval in zip(
                args.p0_probe,
                args.p1_probe,
                args.p0_eval,
                args.p1_eval,
                strict=True,
            )
        ]
        report = build_cache_ab_report(rounds)
        if not report["passed"]:
            print(f"cache_ab_status: {report['status']}")
            print(
                "Prompt Cache A/B did not pass; no acceptance report was written.",
                file=sys.stderr,
            )
            return 1
        bundle = write_cache_ab_report(report, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"Cannot build Prompt Cache A/B report: {exc}", file=sys.stderr)
        return 2
    print(f"cache_ab_status: {report['status']}")
    print(f"json_report: {bundle.json_path}")
    print(f"markdown_report: {bundle.markdown_path}")
    return 0 if report["passed"] else 1


def _settings_for_eval_case(settings: Settings, case: EvalCase) -> Settings:
    budget = settings.budget
    if case.budget:
        budget = BudgetSettings(
            max_turns=_case_int(case, "max_turns", settings.budget.max_turns),
            max_bash_actions=_case_int(case, "max_bash_actions", settings.budget.max_bash_actions),
            max_seconds=_case_int(case, "max_seconds", settings.budget.max_seconds),
            max_action_timeout_sec=_case_int(
                case,
                "max_action_timeout_sec",
                settings.budget.max_action_timeout_sec,
            ),
        )

    context = replace(
        settings.context,
        max_prompt_chars=_context_int(case, "max_prompt_chars", settings.context.max_prompt_chars),
        recent_turns=_context_int(case, "recent_turns", settings.context.recent_turns),
        summary_max_chars=_context_int(case, "summary_max_chars", settings.context.summary_max_chars),
        retention_markers=(
            tuple(str(item) for item in case.context.get("retention_markers", []))
            or settings.context.retention_markers
        ),
    )

    case_policy = settings.policy
    if not case.capability.startswith("hitl"):
        case_policy = PolicySettings(
            require_approval_for_network=False,
            deny_sudo=settings.policy.deny_sudo,
            require_approval_for_destructive=settings.policy.require_approval_for_destructive,
        )

    if case.sandbox_mode not in {"dev", "local"}:
        return Settings(
            provider=settings.provider,
            sandbox=settings.sandbox,
            budget=budget,
            context=context,
            policy=case_policy,
            project=settings.project,
            workspace=settings.workspace,
        )
    sandbox = SandboxSettings(
        image=settings.sandbox.image,
        mode="dev",
        cpus=settings.sandbox.cpus,
        memory=settings.sandbox.memory,
        pids_limit=settings.sandbox.pids_limit,
        network="bridge",
    )
    return Settings(
        provider=settings.provider,
        sandbox=sandbox,
        budget=budget,
        context=context,
        policy=case_policy,
        project=settings.project,
        workspace=settings.workspace,
    )


def _case_int(case: EvalCase, name: str, default: int) -> int:
    value = case.budget.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _context_int(case: EvalCase, name: str, default: int) -> int:
    value = case.context.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _case_constraints(case: EvalCase) -> list[str]:
    constraints: list[str] = []
    if case.writable_paths is not None:
        allowed = ", ".join(case.writable_paths) if case.writable_paths else "none"
        constraints.append(
            f"The sandbox enforces a read-only workspace except these writable paths: {allowed}."
        )
    verification_commands = [
        str(assertion.get("command"))
        for assertion in case.assertions
        if assertion.get("type") == "command" and assertion.get("command")
    ]
    if verification_commands:
        constraints.append(
            "Use these authoritative offline verification commands; do not install a different test runner: "
            + " ; ".join(verification_commands)
        )
    return constraints


def _git_evidence(cwd: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=True,
        ).stdout
        return commit, bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return "", True


def _release_gate_error(
    args: argparse.Namespace,
    git_commit: str,
    worktree_dirty: bool,
    docker_image: str = "python:test@sha256:test",
) -> str:
    if not git_commit:
        return "the workspace is not pinned to a Git commit"
    if worktree_dirty:
        return "the Git worktree has uncommitted changes"
    if args.execute_local:
        return "release acceptance must use Docker"
    if "@sha256:" not in docker_image:
        return "release acceptance requires a Docker image pinned by sha256 digest"
    if args.repeat < 3:
        return "release acceptance requires --repeat 3 or greater"
    if not args.case_names:
        return "release acceptance requires an explicit complete --case matrix"
    return ""


def _cache_probe_release_gate_error(
    args: argparse.Namespace,
    git_commit: str,
    worktree_dirty: bool,
) -> str:
    if not git_commit:
        return "the workspace is not pinned to a Git commit"
    if worktree_dirty:
        return "the Git worktree has uncommitted changes"
    if args.repeat != 5:
        return "formal cache probes require exactly --repeat 5"
    if not getattr(args, "execution_order", None):
        return "formal cache probes require --execution-order"
    return ""


def traces_command(args: argparse.Namespace) -> int:
    runs_root = Path.cwd() / ".minicc" / "runs"
    if not runs_root.exists():
        print(f"No runs found under {runs_root}.")
        return 0

    found = False
    for run_dir in sorted((item for item in runs_root.iterdir() if item.is_dir()), reverse=True):
        trace_path = run_dir / "trace.jsonl"
        metrics_path = run_dir / "metrics.json"
        if trace_path.exists() or metrics_path.exists():
            found = True
            print(f"{run_dir.name}")
            if trace_path.exists():
                print(f"  trace: {trace_path}")
            if metrics_path.exists():
                print(f"  metrics: {metrics_path}")
    if not found:
        print(f"No trace files found under {runs_root}.")
    return 0


def web_command(args: argparse.Namespace) -> int:
    from minicc.server.app import serve_trace_viewer

    settings = load_settings()
    project_root = Path.cwd()
    versions_root = project_root / ".minicc" / "versions"
    catalog = RunCatalog(versions_root)
    index_acceptance_history(project_root, catalog)
    catalog.ensure_version(settings.project.milestone)
    serve_trace_viewer(
        runs_root=project_root / ".minicc" / "runs",
        versions_root=versions_root,
        current_milestone=settings.project.milestone,
        host=args.host,
        port=args.port,
    )
    return 0


def cleanup_command(args: argparse.Namespace) -> int:
    if args.older_than_hours < 0:
        print("--older-than-hours must be non-negative.", file=sys.stderr)
        return 2
    project_root = Path.cwd()
    plan = build_cleanup_plan(
        project_root / ".minicc" / "runs",
        versions_root=project_root / ".minicc" / "versions",
        acceptance_root=project_root / "acceptance",
        older_than=timedelta(hours=args.older_than_hours),
    )
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"cleanup_mode: {mode}")
    print(f"protected_runs: {len(plan.protected_run_ids)}")
    print(f"candidates: {len(plan.candidates)}")
    for candidate in plan.candidates:
        print(f"  {candidate.run_id}: {candidate.reason}")
    result = apply_cleanup_plan(plan, apply=bool(args.apply))
    print(f"deleted: {len(result.deleted_run_ids)}")
    return 0


def _effective_milestone(settings: Settings, args: argparse.Namespace) -> str:
    override = str(getattr(args, "milestone", "") or "").strip()
    if override:
        return override
    output_dir = getattr(args, "output_dir", None)
    if output_dir is not None and str(Path(output_dir).name).startswith("stable-v"):
        return str(Path(output_dir).name)
    return settings.project.milestone


def _secret_fingerprint(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_run_artifact_index(state: RunState) -> Path | None:
    if state.run_dir is None:
        return None
    run_dir = state.run_dir.resolve()
    evidence = {
        "state": str(run_dir / "state.json"),
        "trace": str(run_dir / "trace.jsonl"),
        "metrics": str(run_dir / "metrics.json"),
        "workspace_manifest": str(run_dir / "workspace_manifest.json"),
        "diff": str(run_dir / "artifacts" / "diff.patch"),
        "run_report": str(run_dir / "run_report.json"),
    }
    return write_artifact_index(
        run_dir.parent.parent / "artifacts",
        run_id=state.run_id,
        run_dir=run_dir,
        evidence=evidence,
    )


if __name__ == "__main__":
    raise SystemExit(main())
