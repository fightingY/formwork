from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minicc import __version__
from minicc.config import load_settings
from minicc.core.loop import AgentLoop, LocalCommandExecutor, LoopConfig
from minicc.core.provider import CompletionOptions, OpenAICompatibleProvider
from minicc.core.state import RunState
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.docker_runner import DockerCommandExecutor, DockerSandboxConfig, DockerSandboxRunner
from minicc.sandbox.workspace import prepare_run_workspace, write_workspace_diff


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

    eval_parser = subparsers.add_parser("eval", help="Eval runner entry point placeholder for M6.")
    eval_parser.add_argument("path", nargs="?", default="eval_cases", help="Eval cases directory.")
    eval_parser.set_defaults(handler=eval_command)

    traces_parser = subparsers.add_parser("traces", help="List trace runs placeholder for M5/M6.")
    traces_parser.set_defaults(handler=traces_command)
    return parser


def run_command(args: argparse.Namespace) -> int:
    settings = load_settings()
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
            + "\nSet these environment variables before running a real model.",
            file=sys.stderr,
        )
        return 2

    provider = OpenAICompatibleProvider(
        base_url=settings.base_url or "",
        api_key=settings.api_key or "",
        model=settings.model or "",
    )
    workspace = None
    runner = None
    state = RunState.start(args.goal)
    try:
        if args.no_workspace_copy:
            if not args.execute_local:
                print("--no-workspace-copy requires --execute-local.", file=sys.stderr)
                return 2
            state.workspace_host_path = Path.cwd()
            executor = LocalCommandExecutor()
        else:
            workspace = prepare_run_workspace(Path.cwd(), run_id=state.run_id)
            state.workspace_host_path = workspace.workspace_dir
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

        loop = AgentLoop(
            provider,
            executor,
            config=LoopConfig(
                max_turns=settings.budget.max_turns if args.max_turns is None else args.max_turns,
                max_action_timeout_sec=settings.budget.max_action_timeout_sec,
                model_options=CompletionOptions(
                    temperature=settings.temperature,
                    stream=settings.provider.stream if args.stream is None else args.stream,
                    include_usage=settings.provider.include_usage,
                ),
            ),
        )
        result = loop.run(state)
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

    print(f"run_id: {result.state.run_id}")
    print(f"status: {result.state.status}")
    print(f"turns: {result.state.metrics['turns']}")
    print(f"bash_actions: {result.state.metrics['bash_actions']}")
    print(f"artifact_bytes: {result.state.metrics.get('artifact_bytes', 0)}")
    if workspace is not None:
        print(f"run_dir: {workspace.run_dir}")
        print(f"workspace: {workspace.workspace_dir}")
        print(f"artifacts: {workspace.artifacts_dir}")
    if result.state.open_questions:
        print("question: " + result.state.open_questions[-1])
    if result.state.final_answer:
        print("\n" + result.state.final_answer)
    if result.state.status == "failed" and result.state.state_summary:
        print(result.state.state_summary, file=sys.stderr)
        return 1
    return 0


def eval_command(args: argparse.Namespace) -> int:
    print(
        f"Eval runner is scheduled for M6. Received cases path: {args.path}\n"
        "M1 currently provides the CLI skeleton, provider adapter, action protocol, and minimal loop."
    )
    return 0


def traces_command(args: argparse.Namespace) -> int:
    print(
        "Trace listing is scheduled for the observability milestone. "
        "M1 does not persist trace files yet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
