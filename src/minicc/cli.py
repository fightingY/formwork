from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minicc import __version__
from minicc.config import load_settings
from minicc.core.loop import AgentLoop, DisabledExecutor, LocalCommandExecutor, LoopConfig
from minicc.core.provider import CompletionOptions, OpenAICompatibleProvider
from minicc.core.state import RunState


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
    run_parser = subparsers.add_parser("run", help="Run a goal through the M1 agent loop.")
    run_parser.add_argument("goal", help="User goal for the agent.")
    run_parser.add_argument("--max-turns", type=int, default=8, help="Maximum model turns.")
    run_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Execute bash actions on the host for M1 demos. Docker sandboxing arrives in M2.",
    )
    run_parser.add_argument(
        "--stream",
        action="store_true",
        help="Use OpenAI-compatible streaming responses and request final usage when supported.",
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
    executor = LocalCommandExecutor() if args.execute_local else DisabledExecutor()
    state = RunState.start(args.goal, workspace_host_path=Path.cwd())
    loop = AgentLoop(
        provider,
        executor,
        config=LoopConfig(
            max_turns=args.max_turns,
            model_options=CompletionOptions(
                temperature=settings.temperature,
                stream=args.stream,
                include_usage=True,
            ),
        ),
    )
    result = loop.run(state)

    print(f"run_id: {result.state.run_id}")
    print(f"status: {result.state.status}")
    print(f"turns: {result.state.metrics['turns']}")
    print(f"bash_actions: {result.state.metrics['bash_actions']}")
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
