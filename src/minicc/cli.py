from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minicc import __version__
from minicc.config import BudgetSettings, SandboxSettings, Settings, load_settings
from minicc.core.context import ContextBuilder, ContextConfig
from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig
from minicc.core.provider import CompletionOptions, OpenAICompatibleProvider
from minicc.core.session import SessionManager
from minicc.core.state import RunState, state_path_for_run
from minicc.evals.case import EvalCase
from minicc.evals.runner import copy_report_to_run_root, run_eval_suite
from minicc.memory.feedback import FeedbackMemory
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
    eval_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Run eval bash actions locally instead of Docker.",
    )
    eval_parser.set_defaults(handler=eval_command)

    traces_parser = subparsers.add_parser("traces", help="List runs that have trace or metrics files.")
    traces_parser.set_defaults(handler=traces_command)

    web_parser = subparsers.add_parser("web", help="Serve a read-only trace viewer.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    web_parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    web_parser.set_defaults(handler=web_command)
    return parser


def run_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    workspace = None
    runner = None
    state = RunState.start(args.goal)
    result = None
    try:
        if args.no_workspace_copy:
            if not args.execute_local:
                print("--no-workspace-copy requires --execute-local.", file=sys.stderr)
                return 2
            state.workspace_host_path = Path.cwd()
            executor = LocalCommandExecutor()
        else:
            workspace = prepare_run_workspace(Path.cwd(), run_id=state.run_id)
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
            except Exception as exc:
                print(f"Failed to write workspace diff: {exc}", file=sys.stderr)

    if result is None:
        return 1
    _print_run_result(result.state, workspace.run_dir if workspace is not None else None)
    if result.state.status == "failed":
        return 1
    return 0


def resume_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    state_path = state_path_for_run(args.run_id)
    if not state_path.exists():
        print(f"Run state not found: {state_path}", file=sys.stderr)
        return 2

    session = SessionManager()
    state = session.load(args.run_id)
    if state.status != "waiting_approval":
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

        session.apply_pending_approval_result(state, executor)
        if state.status == "running":
            loop = _build_loop(provider, executor, settings=settings, session=session, state=state)
            result = loop.run(state)
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
            except Exception as exc:
                print(f"Failed to write workspace diff: {exc}", file=sys.stderr)

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
) -> AgentLoop:
    skill_root = (state.workspace_host_path if state and state.workspace_host_path else Path.cwd()) / "skills"
    return AgentLoop(
        provider,
        executor,
        context_builder=ContextBuilder(
            ContextConfig(
                max_prompt_chars=settings.context.max_prompt_chars,
                recent_turns=settings.context.recent_turns,
                artifact_preview_chars=settings.context.artifact_preview_chars,
            ),
            skill_registry=SkillRegistry(skill_root),
            feedback_memory=FeedbackMemory(Path.cwd() / ".minicc" / "memory" / "feedback_rules.jsonl"),
        ),
        policy_chain=build_policy_chain(settings),
        session=session,
        trace=TraceRecorder(trace_path_for(state)) if state is not None else None,
        config=LoopConfig(
            max_turns=settings.budget.max_turns if max_turns is None else max_turns,
            max_action_timeout_sec=settings.budget.max_action_timeout_sec,
            model_options=CompletionOptions(
                temperature=settings.temperature,
                stream=settings.provider.stream if stream is None else stream,
                include_usage=settings.provider.include_usage,
            ),
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
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    runs_root = Path.cwd() / ".minicc" / "runs"

    def agent_runner(case: EvalCase, state: RunState) -> RunState:
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

    result = run_eval_suite(Path(args.path), runs_root=runs_root, agent_runner=agent_runner)
    json_path, markdown_path = copy_report_to_run_root(result, runs_root)
    print(f"eval_status: {'PASS' if result.passed else 'FAIL'}")
    print(f"json_report: {json_path}")
    print(f"markdown_report: {markdown_path}")
    return 0 if result.passed else 1


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

    if case.sandbox_mode not in {"dev", "local"}:
        return Settings(
            provider=settings.provider,
            sandbox=settings.sandbox,
            budget=budget,
            context=settings.context,
            policy=settings.policy,
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
        context=settings.context,
        policy=settings.policy,
    )


def _case_int(case: EvalCase, name: str, default: int) -> int:
    value = case.budget.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

    serve_trace_viewer(runs_root=Path.cwd() / ".minicc" / "runs", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
