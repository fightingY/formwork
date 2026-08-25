from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from minicc import __version__
from minicc.config import (
    BudgetSettings,
    CompactionStrategy,
    PolicySettings,
    PromptLayout,
    ProviderRoute,
    SandboxSettings,
    Settings,
    load_settings,
)
from minicc.core.checkpoint import CheckpointError, CheckpointManager
from minicc.core.context import STABLE_PREFIX, ContextBuilder, ContextConfig
from minicc.core.failover import ProviderFailoverChain
from minicc.core.ledger import (
    apply_cleanup_plan,
    build_cleanup_plan,
    inspect_run,
    new_suite_id,
    write_artifact_index,
)
from minicc.core.loop import AgentLoop, BashExecutor, LoopConfig, TurnProvider
from minicc.core.project_context import inspect_repository, write_repository_profile
from minicc.core.provider import (
    CompletionOptions,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderRegistry,
)
from minicc.core.retry import RetryingTurnProvider
from minicc.core.run_catalog import RunCatalog, index_acceptance_history
from minicc.core.runner import ModelTurnRunner
from minicc.core.session import SessionManager
from minicc.core.session_engine import SessionEngine
from minicc.core.session_store import SessionNotFoundError, SessionStore
from minicc.core.state import RunState, state_path_for_run
from minicc.core.tooling import HybridToolRunner, ToolCallScheduler
from minicc.core.verification import CommandCompletionVerifier, CompletionVerifier

if TYPE_CHECKING:
    from minicc.core.discovery import ModelInfo
from minicc.evals.cache_ab import (
    build_cache_ab_report,
    write_cache_ab_report,
)
from minicc.evals.cache_ab import (
    load_suite_report as load_cache_suite_report,
)
from minicc.evals.cache_probe import load_cache_probe_report
from minicc.evals.cache_probe_runner import (
    CacheProbeRunConfig,
    cache_sequence_namespace,
    run_fixed_cache_probe,
)
from minicc.evals.cache_utilization import (
    build_cache_utilization_report,
    write_cache_utilization_report,
)
from minicc.evals.cache_utilization import (
    failed_criteria as failed_cache_utilization_criteria,
)
from minicc.evals.case import (
    EvalCase,
    discover_cases,
)
from minicc.evals.compaction_ab import (
    build_compaction_ab_report,
    write_compaction_ab_report,
)
from minicc.evals.compaction_ab import (
    load_suite_report as load_compaction_suite_report,
)
from minicc.evals.guidance_ab import build_guidance_ab_report, write_guidance_ab_report
from minicc.evals.memory_ab import load_follow_up_case, run_memory_ab, write_memory_ab_report
from minicc.evals.memory_acceptance import (
    REQUIRED_MEMORY_CASES,
    build_memory_acceptance_report,
    load_memory_suite_report,
    write_memory_acceptance_report,
)
from minicc.evals.meta_review_ab import build_meta_review_ab_report, write_meta_review_ab_report
from minicc.evals.release_report import (
    build_release_report,
    load_context_suite_evidence,
    load_json_evidence,
    write_release_report,
)
from minicc.evals.runner import run_eval_suite, write_eval_report, write_suite_report
from minicc.memory.compaction import SemanticCompactor
from minicc.memory.dedup import L1Deduper
from minicc.memory.escalation import (
    EscalationHook,
    PersonaEscalator,
    PersonaSynthesizer,
    ScenarioEscalator,
    ScenarioSynthesizer,
)
from minicc.memory.feedback import FeedbackMemory
from minicc.memory.l1 import L1Distiller, MemoryStore, MemoryTurnHook, project_db_path
from minicc.memory.working import attach_working_memory
from minicc.meta.reviewer import MetaReviewer, MetaReviewError, load_meta_review
from minicc.multi_agent import SubprocessChildRunProvider, WorkflowCoordinator, childrun_main
from minicc.policy.factory import build_policy_chain
from minicc.sandbox.artifact_store import ArtifactStore
from minicc.sandbox.docker_runner import (
    DockerCommandExecutor,
    DockerSandboxConfig,
    DockerSandboxRunner,
)
from minicc.sandbox.local_runner import LocalCommandExecutor
from minicc.sandbox.workspace import (
    prepare_run_workspace,
    workspace_content_digest,
    write_workspace_diff,
)
from minicc.skills.registry import SkillRegistry, default_skill_roots
from minicc.trace.metrics import write_metrics
from minicc.trace.recorder import TraceRecorder, trace_path_for
from minicc.trace.replay import (
    ReplayError,
    compare_fresh_replay,
    create_replay_case,
    create_replay_case_from_eval_case,
    run_deterministic_replay,
)
from minicc.trace.report import write_run_report
from minicc.trace.transcript import project_trace


def _reconfigure_std_streams() -> None:
    """Point stdout/stderr at UTF-8 with a replacement fallback.

    Model-generated text (final answers, summaries, questions) can contain
    emoji and other characters that the Windows console's default GBK codec
    cannot encode, which makes ``print`` raise ``UnicodeEncodeError`` and
    crashes the CLI *after* the run has already been persisted. UTF-8 covers
    the whole Unicode range, and ``errors="replace"`` guards against lone
    surrogates from a malformed response. Streams without a ``reconfigure``
    attribute (e.g. ``StringIO`` in tests) are left untouched.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _reconfigure_std_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="formwork",
        description="Formwork CodeAct Agent Runtime.",
    )
    parser.add_argument("--version", action="version", version=f"formwork {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run a goal through the Formwork agent loop.")
    run_parser.add_argument("goal", help="User goal for the agent.")
    run_parser.add_argument("--milestone", default=None, help="Override project.milestone for run indexing.")
    run_parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help=(
            "Read a repository from this path, copy it into Formwork's run root, and leave the "
            "source directory untouched."
        ),
    )
    run_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Execute bash actions on the host for development demos instead of Docker.",
    )
    run_parser.add_argument(
        "--verify-command",
        action="append",
        default=[],
        help=(
            "Run this pre-bound command when the model requests final; repeat the option "
            "to bind multiple completion checks."
        ),
    )
    run_parser.add_argument(
        "--verification-timeout-sec",
        type=int,
        default=120,
        help="Timeout for each pre-bound completion verification command.",
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
    run_parser.add_argument(
        "--profile",
        choices=("baseline-bash", "hybrid-v3.6", "multi-agent-v4"),
        default=None,
        help="Override tooling profile for this run.",
    )
    run_parser.add_argument(
        "--follow-up-from",
        default=None,
        metavar="RUN_ID",
        help="Explicitly attach grounded working memory captured by a completed source run.",
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

    models_parser = subparsers.add_parser(
        "models",
        help="Discover models available on a provider route (bounded GET /models).",
    )
    models_parser.add_argument(
        "route",
        nargs="?",
        default=None,
        help="Route name from providers: (defaults to default_provider).",
    )
    models_parser.add_argument(
        "--probe-key",
        default=None,
        help="Temporary API key to probe with; never persisted.",
    )
    models_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit a single JSON array instead of a plain list.",
    )
    models_parser.set_defaults(handler=models_command)

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
        choices=("p0", "p1", "p2"),
        default=None,
        help="Cache experiment variant: p0=rebuild, p1=windowed append, p2=epoch append.",
    )
    eval_parser.add_argument(
        "--cache-sequence-id",
        default=None,
        help="Within-round cache experiment namespace shared by P0/P1, for example round-1.",
    )
    eval_parser.add_argument(
        "--execution-order",
        choices=("p0-first", "p1-first", "p2-first"),
        default=None,
        help="Record the balanced P0/P1 order used for this cache experiment round.",
    )
    eval_parser.add_argument(
        "--guidance-variant",
        choices=("a0", "a1"),
        default=None,
        help="V3.2 guidance experiment: a0=disabled, a1=relevant Skill/Feedback selection.",
    )
    eval_parser.add_argument(
        "--guidance-sequence-id",
        default=None,
        help="Within-round namespace shared by the V3.2 A0/A1 guidance arms.",
    )
    eval_parser.add_argument(
        "--guidance-execution-order",
        choices=("a0-first", "a1-first"),
        default=None,
        help="Record the balanced A0/A1 order used for the V3.2 guidance experiment.",
    )
    eval_parser.add_argument(
        "--guidance-feedback-path",
        default="guidance/feedback_rules.jsonl",
        help="Workspace-relative, commit-bound feedback rule source for the A1 arm.",
    )
    eval_parser.set_defaults(handler=eval_command)

    memory_eval_parser = subparsers.add_parser(
        "memory-eval",
        help="Run the paired two-stage V2.2 working-memory A/B evaluation.",
    )
    memory_eval_parser.add_argument("path", help="Follow-up case directory or case.yaml path.")
    memory_eval_parser.add_argument("--milestone", default=None)
    memory_eval_parser.add_argument("--repeat", type=int, default=3)
    memory_eval_parser.add_argument(
        "--execution-order",
        choices=("alternating", "m0-first", "m1-first"),
        default="alternating",
    )
    memory_eval_parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Run bash actions locally instead of in Docker.",
    )
    memory_eval_parser.add_argument(
        "--release-gate",
        action="store_true",
        help="Require the canonical V2.2 case, clean Git authority, Docker, and repeat=3.",
    )
    memory_eval_parser.set_defaults(handler=memory_eval_command)

    memory_report_parser = subparsers.add_parser(
        "memory-report",
        help="Verify and aggregate the three formal V2.2 memory suites.",
    )
    memory_report_parser.add_argument(
        "--report",
        action="append",
        type=Path,
        required=True,
        help="Formal memory suite report.json; provide exactly one for each M01/M02/M03.",
    )
    memory_report_parser.add_argument("--output-dir", type=Path, required=True)
    memory_report_parser.set_defaults(handler=memory_report_command)

    release_report_parser = subparsers.add_parser(
        "release-report",
        help="Build the four-dimension V3.0 release evidence report.",
    )
    release_report_parser.add_argument(
        "--system-report",
        type=Path,
        default=Path("acceptance/stable-v1.3/eval_report.json"),
    )
    release_report_parser.add_argument(
        "--context-report",
        type=Path,
        default=Path("acceptance/stable-v2.1/context-compaction-ab/report.json"),
    )
    release_report_parser.add_argument(
        "--memory-report",
        type=Path,
        default=Path("acceptance/stable-v2.2/report.json"),
    )
    release_report_parser.add_argument(
        "--resume-report",
        type=Path,
        default=Path("acceptance/stable-v2.0/checkpoint_report.json"),
    )
    release_report_parser.add_argument(
        "--suites-root",
        type=Path,
        default=Path(".minicc/suites"),
        help="Raw immutable suite root used to resolve V2.1 run IDs.",
    )
    release_report_parser.add_argument("--output-dir", type=Path, default=None)
    release_report_parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Write an EMPTY/experimental dimension instead of failing on a missing input.",
    )
    release_report_parser.add_argument(
        "--release-gate",
        action="store_true",
        help="Require a clean V3.0 formal system suite and acceptance/stable-v3.0 output.",
    )
    release_report_parser.set_defaults(handler=release_report_command)

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
        choices=("p0", "p1", "p2"),
        required=True,
        help="p0=rebuild, p1=windowed append, p2=epoch append.",
    )
    cache_probe_parser.add_argument(
        "--repeat",
        type=int,
        default=5,
        help=(
            "Number of growing fixed-sequence requests "
            "(formal minimum: 5; V2.1.2 requires exactly 12)."
        ),
    )
    cache_probe_parser.add_argument(
        "--milestone",
        default=None,
        help="Override project.milestone recorded in probe evidence.",
    )
    cache_probe_parser.add_argument(
        "--execution-order",
        choices=("p0-first", "p1-first", "p2-first"),
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
        help=(
            "Require a clean immutable Git commit and the milestone's formal "
            "repeat count (V2.1.2: exactly 12)."
        ),
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

    cache_utilization_parser = subparsers.add_parser(
        "cache-utilization-report",
        help="Validate exactly two V2.1.2 P1/P2 long-cache acceptance rounds.",
    )
    for flag, help_text in (
        ("--p1-probe", "P1 fixed-long probe report.json; repeat once per round."),
        ("--p2-probe", "P2 fixed-long probe report.json; repeat once per round."),
        ("--p1-eval", "P1 C02/C07 suite report.json; repeat once per round."),
        ("--p2-eval", "P2 C02/C07 suite report.json; repeat once per round."),
    ):
        cache_utilization_parser.add_argument(
            flag,
            action="append",
            type=Path,
            required=True,
            help=help_text,
        )
    cache_utilization_parser.add_argument("--output-dir", type=Path, required=True)
    cache_utilization_parser.set_defaults(handler=cache_utilization_report_command)

    traces_parser = subparsers.add_parser("traces", help="List runs that have trace or metrics files.")
    traces_parser.set_defaults(handler=traces_command)
    transcript_parser = subparsers.add_parser("transcript", help="Project a trace.jsonl into transcript artifacts.")
    transcript_parser.add_argument("trace", type=Path, help="Path to trace.jsonl.")
    transcript_parser.add_argument("--output-dir", type=Path, default=None)
    transcript_parser.set_defaults(handler=transcript_command)

    replay_parser = subparsers.add_parser(
        "replay",
        help="Package a run as a replay case or execute deterministic/fresh replay.",
    )
    replay_sub = replay_parser.add_subparsers(dest="replay_subcommand")
    replay_create = replay_sub.add_parser(
        "create", help="Create an immutable replay case from a completed run."
    )
    replay_create.add_argument("run_id", help="Run id below .minicc/runs, or a run directory path.")
    replay_create.add_argument("--output-dir", type=Path, default=None)
    replay_create.add_argument("--overwrite", action="store_true")
    replay_create.set_defaults(handler=replay_create_command)

    replay_run = replay_sub.add_parser(
        "run", help="Run deterministic replay, optionally followed by a fresh Runtime replay."
    )
    replay_run.add_argument("case", type=Path, help="Replay case directory or case.json path.")
    replay_run.add_argument("--fresh", action="store_true", help="Also rerun the current Runtime.")
    replay_run.add_argument("--execute-local", action="store_true", help="Use local execution instead of Docker for fresh replay.")
    replay_run.add_argument("--docker-image", default=None, help="Override the Docker image for fresh replay.")
    replay_run.add_argument("--profile", choices=("baseline-bash", "hybrid-v3.6", "multi-agent-v4"), default=None)
    replay_run.add_argument("--verify-command", action="append", default=[], help="Pre-bound verifier command for fresh replay; repeatable.")
    replay_run.add_argument("--verification-timeout-sec", type=int, default=120)
    replay_run.add_argument("--output-dir", type=Path, default=None)
    replay_run.add_argument("--json", action="store_true", dest="json_output")
    replay_run.set_defaults(handler=replay_run_command)

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

    meta_parser = subparsers.add_parser(
        "meta-review",
        help="Review immutable evidence from one completed run without changing that run.",
    )
    meta_parser.add_argument("run_id", help="Run id below .minicc/runs.")
    meta_parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the deterministic diagnostic instead of a model (not formal evidence).",
    )
    meta_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Review root (default: .minicc/meta-reviews).",
    )
    meta_parser.set_defaults(handler=meta_review_command)

    meta_report_parser = subparsers.add_parser(
        "meta-review-report",
        help="Build the V3.1 disabled/enabled Meta Review acceptance report.",
    )
    meta_report_parser.add_argument("--disabled-suite", type=Path, required=True)
    meta_report_parser.add_argument("--enabled-suite", type=Path, required=True)
    meta_report_parser.add_argument(
        "--review",
        dest="reviews",
        action="append",
        type=Path,
        required=True,
        help="Meta review directory or report.json; repeat once per enabled run.",
    )
    meta_report_parser.add_argument("--output-dir", type=Path, required=True)
    meta_report_parser.add_argument("--release-gate", action="store_true")
    meta_report_parser.set_defaults(handler=meta_review_report_command)

    guidance_report_parser = subparsers.add_parser(
        "guidance-report",
        help="Verify and aggregate the V3.2 disabled/enabled guidance suites.",
    )
    guidance_report_parser.add_argument("--disabled-suite", type=Path, required=True)
    guidance_report_parser.add_argument("--enabled-suite", type=Path, required=True)
    guidance_report_parser.add_argument("--output-dir", type=Path, required=True)
    guidance_report_parser.add_argument("--release-gate", action="store_true")
    guidance_report_parser.set_defaults(handler=guidance_report_command)

    web_parser = subparsers.add_parser("web", help="Serve a read-only trace viewer.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    web_parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    web_parser.set_defaults(handler=web_command)
    childrun_parser = subparsers.add_parser("childrun", help="Run a V4 child over stdin/stdout JSONL.")
    childrun_parser.set_defaults(handler=childrun_command)

    # --- V5 experimental: conversation sessions (docs/V5_0_SESSION_CHAT_REMODEL_PLAN.md) ---
    session_parser = subparsers.add_parser(
        "session", help="Manage conversation sessions (V5, experimental)."
    )
    session_sub = session_parser.add_subparsers(dest="session_subcommand")
    session_new = session_sub.add_parser("new", help="Create a new session.")
    session_new.add_argument("--project-root", type=Path, default=None, help="Project directory (default: cwd).")
    session_new.add_argument("--title", default="", help="Optional session title.")
    session_new.set_defaults(handler=session_command)
    session_list = session_sub.add_parser("list", help="List all sessions.")
    session_list.set_defaults(handler=session_command)
    session_show = session_sub.add_parser("show", help="Show one session's transcript.")
    session_show.add_argument("session_id")
    session_show.set_defaults(handler=session_command)
    session_rename = session_sub.add_parser("rename", help="Rename a session.")
    session_rename.add_argument("session_id")
    session_rename.add_argument("title")
    session_rename.set_defaults(handler=session_command)
    for name, help_text in (("switch", "Set the current session."), ("resume", "Point to a session to continue.")):
        sub = session_sub.add_parser(name, help=help_text)
        sub.add_argument("session_id")
        sub.set_defaults(handler=session_command)

    chat_parser = subparsers.add_parser(
        "chat", help="Start a conversational chat REPL against a session (V5, experimental)."
    )
    chat_parser.add_argument("--project-root", type=Path, default=None, help="Project directory (default: cwd).")
    chat_parser.add_argument(
        "--session",
        "--resume",
        dest="session_id",
        default=None,
        help="Session id to continue; defaults to the current (switched) session, else a new one.",
    )
    chat_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Serve the web chat UI on this port instead of the terminal REPL.",
    )
    chat_parser.set_defaults(handler=chat_command)
    return parser


def models_command(args: argparse.Namespace) -> int:
    # V4.1 M5：模型发现在解析到该子命令时才按需 import，避免 `core.discovery` 的
    # httpx 边界进入其它命令的启动路径。
    from minicc.core.discovery import discover_models

    settings = load_settings()
    route_name = args.route or settings.default_provider
    route = settings.providers.get(route_name)
    if route is None:
        print(f"Unknown provider route: {route_name!r}", file=sys.stderr)
        return 2
    api_key = args.probe_key or route.api_key
    if not api_key:
        print(
            f"Route {route_name!r} has no API key; pass --probe-key or set its api_key_env.",
            file=sys.stderr,
        )
        return 2

    try:
        models = discover_models(
            route.base_url,
            api_key,
            headers=route.headers or None,
            timeout_ms=route.timeout_ms,
        )
    except ProviderError as exc:
        print(f"Model discovery failed ({exc.failure.code}): {exc.failure.message}", file=sys.stderr)
        return 1

    if not models:
        print("No models discovered.", file=sys.stderr)
        return 1
    _print_models(models, json_output=args.json_output)
    return 0


def _print_models(models: list[ModelInfo], *, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(
                [
                    {
                        "id": model.id,
                        "context_window": model.context_window,
                        "max_output_tokens": model.max_output_tokens,
                    }
                    for model in models
                ],
                ensure_ascii=False,
            )
        )
        return
    for model in models:
        extras = []
        if model.context_window is not None:
            extras.append(f"context_window={model.context_window}")
        if model.max_output_tokens is not None:
            extras.append(f"max_output_tokens={model.max_output_tokens}")
        suffix = ("  " + "  ".join(extras)) if extras else ""
        print(model.id + suffix)


def childrun_command(args: argparse.Namespace) -> int:
    return childrun_main(sys.stdin, sys.stdout)


def transcript_command(args: argparse.Namespace) -> int:
    try:
        json_path, markdown_path = project_trace(args.trace, args.output_dir)
    except OSError as exc:
        print(f"Transcript projection failed: {exc}", file=sys.stderr)
        return 1
    print(f"transcript_jsonl: {json_path}")
    print(f"transcript_md: {markdown_path}")
    return 0


def replay_create_command(args: argparse.Namespace) -> int:
    raw = Path(str(args.run_id))
    try:
        if raw.is_file() and raw.name in {"case.yaml", "case.yml"}:
            case_dir = create_replay_case_from_eval_case(
                raw,
                output_dir=args.output_dir,
                overwrite=bool(args.overwrite),
            )
        elif raw.is_dir() and (raw / "case.yaml").is_file() and not (raw / "state.json").is_file():
            case_dir = create_replay_case_from_eval_case(
                raw,
                output_dir=args.output_dir,
                overwrite=bool(args.overwrite),
            )
        else:
            run_dir = raw if raw.is_dir() else Path.cwd() / ".minicc" / "runs" / str(args.run_id)
            case_dir = create_replay_case(
                run_dir,
                output_dir=args.output_dir,
                overwrite=bool(args.overwrite),
            )
    except (ReplayError, OSError) as exc:
        print(f"Replay case creation failed: {exc}", file=sys.stderr)
        return 1
    manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    print(f"replay_case: {case_dir}")
    print(f"case_id: {manifest.get('case_id', case_dir.name)}")
    print(f"deterministic_eligible: {manifest.get('deterministic_eligible', False)}")
    print(f"fresh_eligible: {manifest.get('fresh_eligible', False)}")
    return 0


def replay_run_command(args: argparse.Namespace) -> int:
    case = Path(args.case)
    if case.name == "case.json":
        case = case.parent
    case = case.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else case
    try:
        deterministic = run_deterministic_replay(case, output_dir=output_dir)
    except (ReplayError, OSError, ValueError) as exc:
        print(f"Deterministic replay failed: {exc}", file=sys.stderr)
        return 1

    fresh = None
    fresh_return_code = 0
    if args.fresh:
        manifest_path = case / "case.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            goal = str(manifest.get("goal") or "")
            fixture = case / "workspace"
            if not goal or not fixture.is_dir():
                raise ReplayError("Fresh replay requires a goal and a workspace fixture.")
            if manifest.get("fresh_eligible") is not True:
                raise ReplayError("Replay case does not contain a usable baseline workspace fixture.")
        except (OSError, json.JSONDecodeError, ReplayError) as exc:
            print(f"Fresh replay setup failed: {exc}", file=sys.stderr)
            return 1

        before = {
            path.name
            for path in (Path.cwd() / ".minicc" / "runs").iterdir()
            if path.is_dir()
        } if (Path.cwd() / ".minicc" / "runs").is_dir() else set()
        run_args = argparse.Namespace(
            goal=goal,
            milestone="replay",
            source_dir=fixture,
            execute_local=bool(args.execute_local),
            verify_command=list(
                args.verify_command
                or manifest.get("verification_commands")
                or []
            ),
            verification_timeout_sec=(
                int(args.verification_timeout_sec)
                if args.verification_timeout_sec != 120
                else int(manifest.get("verification_timeout_sec") or 120)
            ),
            no_workspace_copy=False,
            docker_image=args.docker_image,
            stream=None,
            profile=args.profile or manifest.get("profile"),
            follow_up_from=None,
            interrupt_after_steps=None,
        )
        fresh_return_code = run_command(run_args)
        runs_root = Path.cwd() / ".minicc" / "runs"
        candidates = [
            path for path in runs_root.iterdir()
            if path.is_dir() and path.name not in before and (path / "state.json").is_file()
        ] if runs_root.is_dir() else []
        if not candidates:
            print("Fresh replay did not produce a run directory.", file=sys.stderr)
            return 1
        fresh_run = max(candidates, key=lambda path: path.stat().st_mtime)
        try:
            fresh = compare_fresh_replay(case, fresh_run, output_dir=output_dir)
        except (ReplayError, OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Fresh replay comparison failed: {exc}", file=sys.stderr)
            return 1

    case_manifest = json.loads((case / "case.json").read_text(encoding="utf-8"))
    deterministic_required = case_manifest.get("source_kind") != "eval_case"
    combined = {
        "schema_version": 1,
        "case_id": deterministic.case_id,
        "passed": (
            (deterministic.passed or not deterministic_required)
            and (fresh.passed if fresh is not None else deterministic_required)
        ),
        "deterministic_required": deterministic_required,
        "modes": {
            "deterministic": deterministic.report,
            "fresh": fresh.report if fresh is not None else None,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_dir / "replay_report.json"
    combined_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    combined_md = [
        f"# Replay Report: `{deterministic.case_id}`",
        "",
        f"- Overall passed: `{combined['passed']}`",
        f"- Deterministic passed: `{deterministic.passed}`",
    ]
    if fresh is not None:
        combined_md.append(f"- Fresh passed: `{fresh.passed}`")
    combined_md.extend([
        "",
        f"- Deterministic report: `{deterministic.report_path.name}`",
        *([f"- Fresh report: `{fresh.report_path.name}"] if fresh is not None else []),
    ])
    (output_dir / "replay_report.md").write_text("\n".join(combined_md) + "\n", encoding="utf-8")
    if args.json_output:
        print(json.dumps(combined, ensure_ascii=False, indent=2))
    else:
        print(f"replay_case: {case}")
        print(f"deterministic_status: {'PASS' if deterministic.passed else 'FAIL'}")
        if fresh is not None:
            print(f"fresh_status: {'PASS' if fresh.passed else 'FAIL'}")
            print(f"fresh_run: {fresh.report.get('fresh_run_dir', '')}")
        print(f"replay_report: {combined_path}")
    return 0 if combined["passed"] and fresh_return_code == 0 else 1


def run_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    if getattr(args, "profile", None):
        settings = replace(settings, tooling=replace(settings.tooling, profile=args.profile))
    milestone = _effective_milestone(settings, args)
    catalog = RunCatalog(Path.cwd() / ".minicc" / "versions")
    source_arg = getattr(args, "source_dir", None)
    source_dir = (source_arg or Path.cwd()).resolve()
    if not source_dir.is_dir():
        print(f"Source directory does not exist: {source_dir}", file=sys.stderr)
        return 2
    if source_arg is not None and args.no_workspace_copy:
        print("--source-dir cannot be used with --no-workspace-copy.", file=sys.stderr)
        return 2
    raw_verification_commands = getattr(args, "verify_command", []) or []
    verification_commands = tuple(str(command).strip() for command in raw_verification_commands)
    if any(not command for command in verification_commands):
        print("--verify-command cannot be empty.", file=sys.stderr)
        return 2
    verification_timeout_sec = int(getattr(args, "verification_timeout_sec", 120))
    if verification_timeout_sec <= 0:
        print("--verification-timeout-sec must be positive.", file=sys.stderr)
        return 2
    source_digest_before = workspace_content_digest(source_dir)
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2

    workspace = None
    runner = None
    state = RunState.start(args.goal, milestone=milestone, stage="daily_development")
    completion_verifier = None
    if verification_commands:
        completion_verifier = CommandCompletionVerifier(
            commands=verification_commands,
            timeout_sec=verification_timeout_sec,
        )
        state.constraints.append(
            "Completion is accepted only after these pre-bound verification commands pass: "
            + " ; ".join(verification_commands)
        )
        verifier_payload = json.dumps(
            {
                "commands": verification_commands,
                "timeout_sec": verification_timeout_sec,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        state.metrics["completion_verifier_sha256"] = hashlib.sha256(
            verifier_payload
        ).hexdigest()
        state.metrics["completion_verifier_commands"] = list(verification_commands)
        state.metrics["completion_verifier_timeout_sec"] = verification_timeout_sec
    result = None
    try:
        follow_up_from = getattr(args, "follow_up_from", None)
        if follow_up_from and args.no_workspace_copy:
            print("--follow-up-from cannot be used with --no-workspace-copy.", file=sys.stderr)
            return 2
        if args.no_workspace_copy:
            if not args.execute_local:
                print("--no-workspace-copy requires --execute-local.", file=sys.stderr)
                return 2
            state.workspace_host_path = Path.cwd()
            executor: BashExecutor = LocalCommandExecutor()
        else:
            workspace = prepare_run_workspace(
                source_dir,
                run_id=state.run_id,
                runs_root=(Path.cwd() / ".minicc" / "runs") if source_arg is not None else None,
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

        _attach_repository_context(state, state.workspace_host_path or Path.cwd())

        if follow_up_from:
            attach_working_memory(
                state,
                str(follow_up_from),
                runs_root=Path.cwd() / ".minicc" / "runs",
            )

        session = SessionManager()
        session.save(state)
        loop = _build_loop(
            provider,
            executor,
            settings=settings,
            session=session,
            state=state,
            stream=args.stream,
            interrupt_after_steps=getattr(args, "interrupt_after_steps", None),
            completion_verifier=completion_verifier,
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
        try:
            source_digest_after = workspace_content_digest(source_dir)
            state.metrics["source_content_sha256_before"] = source_digest_before
            state.metrics["source_content_sha256_after"] = source_digest_after
            state.metrics["source_workspace_unchanged"] = source_digest_after == source_digest_before
            if source_digest_after != source_digest_before:
                state.status = "failed"
                state.state_summary = "Source repository changed during isolated execution."
                print(state.state_summary, file=sys.stderr)
        except OSError as exc:
            state.status = "failed"
            state.state_summary = f"Cannot verify source repository integrity: {exc}"
            print(state.state_summary, file=sys.stderr)
        SessionManager().save(state)
        write_metrics(state)
        write_run_report(state)
        if state.run_dir is not None:
            trace_file = state.run_dir / "trace.jsonl"
            if trace_file.exists():
                try:
                    project_trace(trace_file, state.run_dir)
                except OSError as exc:
                    print(f"Failed to finalize transcript: {exc}", file=sys.stderr)
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
            executor: BashExecutor = LocalCommandExecutor(
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
            if state.workspace_host_path is not None and not state.repository_profile:
                _attach_repository_context(state, state.workspace_host_path, trace=trace)
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
    print(f"Approved pending action for run {args.run_id}. Use `uv run formwork resume {args.run_id}` to continue.")
    return 0


def deny_command(args: argparse.Namespace) -> int:
    session = SessionManager()
    state = _load_waiting_state(session, args.run_id, require_pending_action=False)
    if state is None:
        return 2
    session.deny(state, args.reason)
    print(f"Denied pending action for run {args.run_id}. Use `uv run formwork resume {args.run_id}` to continue.")
    return 0


# --- V5 conversation sessions (experimental) ---------------------------------
# `session` manages the persisted conversation records; `chat` runs the
# re-entrant REPL backed by SessionEngine.  Both are marked experimental until
# deterministic tests + real-model acceptance land (CLAUDE.md convention).


def _session_store() -> SessionStore:
    return SessionStore()


def _current_session_path() -> Path:
    return Path.cwd() / ".minicc" / "sessions" / "current"


def _current_session_id() -> str | None:
    path = _current_session_path()
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _set_current_session_id(session_id: str) -> None:
    path = _current_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_id + "\n", encoding="utf-8")


def _show_session(store: SessionStore, session_id: str) -> int:
    try:
        record = store.load(session_id)
    except SessionNotFoundError:
        print(f"Session not found: {session_id}", file=sys.stderr)
        return 2
    print(f"session: {record.session_id}")
    print(f"project: {record.project_root}")
    print(f"title:   {record.title or '(untitled)'}")
    print(f"turns:   {len(record.turns)}")
    print("--- transcript ---")
    transcript = store.read_transcript(session_id)
    if not transcript:
        print("(empty)")
    for message in transcript:
        print(f"[{message.role}] {message.content}")
    return 0


def session_command(args: argparse.Namespace) -> int:
    store = _session_store()
    sub = args.session_subcommand

    if sub == "new":
        project_root = args.project_root or Path.cwd()
        record = store.create(project_root, title=args.title)
        _set_current_session_id(record.session_id)
        print(f"Created session {record.session_id}")
        print(f"  project: {record.project_root}")
        if record.title:
            print(f"  title:   {record.title}")
        print("Continue with: uv run formwork chat --session " + record.session_id)
        return 0

    if sub == "list":
        current = _current_session_id()
        records = store.list_sessions()
        if not records:
            print("No sessions yet. Create one with: uv run formwork session new")
            return 0
        for record in records:
            marker = "*" if record.session_id == current else " "
            title = record.title or "(untitled)"
            print(f"{marker} {record.session_id}  {record.updated_at}  {len(record.turns)} turn(s)  {title}")
            print(f"    project: {record.project_root}")
        return 0

    if sub == "show":
        return _show_session(store, args.session_id)

    if sub == "rename":
        try:
            record = store.rename(args.session_id, args.title)
        except SessionNotFoundError:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            return 2
        print(f"Renamed {record.session_id} -> {record.title}")
        return 0

    if sub in {"switch", "resume"}:
        try:
            store.load(args.session_id)
        except SessionNotFoundError:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            return 2
        _set_current_session_id(args.session_id)
        print(f"Now using session {args.session_id}. Continue with: uv run formwork chat")
        return 0

    print("Missing session subcommand.", file=sys.stderr)
    return 2


def chat_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2
    store = _session_store()
    project_root = (args.project_root or Path.cwd()).resolve()
    if not project_root.is_dir():
        print(f"Project root does not exist: {project_root}", file=sys.stderr)
        return 2

    session_id = args.session_id or _current_session_id()
    if session_id is not None and not store.exists(session_id):
        print(f"Session not found: {session_id}", file=sys.stderr)
        return 2
    if session_id is None:
        record = store.create(project_root)
        _set_current_session_id(record.session_id)
        session_id = record.session_id
        print(f"Created session {session_id} (project: {project_root})")

    executor: BashExecutor = LocalCommandExecutor()

    memory_store: MemoryStore | None = None
    memory_hook: MemoryTurnHook | None = None
    if settings.memory.enabled:
        memory_store, memory_hook = _build_memory_subsystem(settings, provider, project_root)

    def loop_factory(state: RunState) -> AgentLoop:
        _attach_repository_context(state, state.workspace_host_path or project_root)
        return _build_loop(
            provider,
            executor,
            settings=settings,
            session=SessionManager(),
            state=state,
            memory_store=memory_store,
        )

    if args.port is not None:
        from minicc.server.chat import serve_chat

        print(f"Serving chat for session {session_id} (project: {project_root})")

        def engine_factory() -> SessionEngine:
            # Deferred mode: no on_approval callback, so a gated destructive
            # command pauses the turn as waiting_approval and the web UI
            # resolves it through the approve/deny endpoints.
            return SessionEngine(
                store,
                loop_factory=loop_factory,
                executor=executor,
                on_turn_end=memory_hook,
            )

        serve_chat(store=store, engine_factory=engine_factory, port=args.port)
        return 0

    def on_approval(state: RunState) -> str:
        command = state.pending_action.command if state.pending_action else ""
        question = state.approval_question or command
        sys.stdout.write(f"approve? [{question}] (y/n/deny <reason>): ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if line == "":
            return "deny"
        line = line.strip().lower()
        if line in {"y", "yes", "approve"}:
            return "approve"
        if line in {"n", "no", "deny"}:
            return "deny"
        return f"deny: {line}"

    engine = SessionEngine(
        store,
        loop_factory=loop_factory,
        executor=executor,
        on_approval=on_approval,
        on_turn_end=memory_hook,
    )
    print("Chat mode (experimental): type a message; empty line or Ctrl-D exits.")
    print("Run in a real project directory; destructive commands ask for approval.")
    while True:
        sys.stdout.write("you> ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if line == "":
            break
        line = line.strip()
        if not line:
            continue
        try:
            turn = engine.submit_turn(session_id, line)
        except Exception as exc:  # one bad turn must not kill the REPL
            print(f"Turn failed: {exc}", file=sys.stderr)
            continue
        print(f"agent> {turn.assistant_reply}")
        print(f"       [run {turn.run_id}, status {turn.status}]")
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


def _route_provider(route: ProviderRoute) -> OpenAICompatibleProvider:
    """Construct a single-attempt adapter for one route via the registry.

    The registry is the single factory surface for upstream adapters; route
    fields become constructor arguments here, while per-route retry policy
    stays on the route object for the retry executor to consume.
    """
    return ProviderRegistry().build(
        route_name=route.name,
        base_url=route.base_url,
        api_key=route.api_key,
        model=route.model,
        timeout_ms=route.timeout_ms,
        headers=route.headers or None,
        provider_name=route.effective_display_name,
        stream_idle_timeout_ms=route.stream_idle_timeout_ms,
    )


def _build_provider_or_print_error(settings: Settings) -> OpenAICompatibleProvider | None:
    route = settings.default_route
    return _route_provider(route)


def _build_turn_provider(
    runner: ModelTurnRunner,
    default_adapter: OpenAICompatibleProvider,
    settings: Settings,
) -> TurnProvider:
    """Assemble the failure-step executor for the agent loop's ``turn_provider`` seam.

    V4.1 的编排层组装点：没有 ``failover`` 块时是单 route 的 :class:`RetryingTurnProvider`
    （默认 adapter + 该 route 的 retry_policy）；配了 ``failover`` 则换成
    :class:`ProviderFailoverChain`（按 chain 逐 route 构造 adapter，复用同一个 runner）。
    trace 直接复用 runner 里已解析好的 TraceRecorder。
    """
    trace = runner.trace
    if settings.failover is not None:
        routes = [
            (
                route_name,
                _route_provider(settings.providers[route_name]),
                settings.providers[route_name].retry_policy,
            )
            for route_name in settings.failover.chain
        ]
        return ProviderFailoverChain(
            runner,
            routes=routes,
            on=settings.failover.on,
            max_hops=settings.failover.max_hops,
            trace=trace,
        )
    route = settings.default_route
    return RetryingTurnProvider(
        runner,
        route_name=route.name,
        provider=default_adapter,
        policy=route.retry_policy,
        trace=trace,
    )


def _child_route(settings: Settings) -> ProviderRoute:
    child = settings.child
    if child is None:
        return settings.default_route
    return settings.providers[child.provider]


def _child_model(settings: Settings) -> str | None:
    route = _child_route(settings)
    if settings.child is not None and settings.child.model:
        return settings.child.model
    return route.model


def _aux_route(settings: Settings) -> ProviderRoute:
    """离线辅助模型（Meta Review / 语义压缩）走的 route；缺省回退主 route。"""
    aux = settings.aux
    if aux is None:
        return settings.default_route
    return settings.providers[aux.provider]


def _aux_model(settings: Settings) -> str:
    route = _aux_route(settings)
    if settings.aux is not None and settings.aux.model:
        return settings.aux.model
    return route.model


def _build_aux_provider(settings: Settings) -> OpenAICompatibleProvider:
    """构造 aux route 的 adapter，供 Meta Review / 语义压缩等辅助模型调用使用。"""
    return _route_provider(_aux_route(settings))


def _build_memory_subsystem(
    settings: Settings,
    provider: OpenAICompatibleProvider,
    project_root: Path,
) -> tuple[MemoryStore | None, MemoryTurnHook | None]:
    """Construct the V5.1 L1 memory store + turn-end hook, or degrade to (None, None).

    Memory is a best-effort enhancement: if the SQLite/FTS5 store cannot be
    created (unsupported sqlite, unwritable ``.minicc/memory``), the session
    simply runs without memory rather than failing (plan §4.5).
    """
    try:
        store = MemoryStore(project_db_path(project_root))
        store.initialize()
    except Exception as exc:  # noqa: BLE001 — memory must never block a run
        print(f"Memory subsystem unavailable; continuing without it: {exc}", file=sys.stderr)
        return None, None
    distiller = L1Distiller(provider)
    deduper: L1Deduper | None = None
    escalator: EscalationHook | None = None
    if settings.memory.enabled:
        deduper = L1Deduper(provider)
        persona = PersonaEscalator(
            store,
            PersonaSynthesizer(provider),
            persona_threshold=settings.memory.persona_confirm_threshold,
        )
        scenario = ScenarioEscalator(
            store,
            ScenarioSynthesizer(provider),
            scenario_threshold=settings.memory.scenario_cluster_threshold,
        )
        escalator = EscalationHook(persona=persona, scenario=scenario)
    hook = MemoryTurnHook(
        store,
        distiller,
        distill_every_n_turns=settings.memory.distill_every_n_turns,
        escalator=escalator,
        deduper=deduper,
    )
    return store, hook


def _provider_summary(settings: Settings) -> dict[str, Any]:
    """Aggregate the default route into the report-configuration contract keys.

    V4.1 去掉扁平 provider 配置后，这些散落在多处 report configuration 里的
    provider 元数据统一从默认 route 派生；temperature/stream/include_usage 不再有
    配置字段，固定为 CompletionOptions 的缺省值。
    """
    route = settings.default_route
    return {
        "base_url": route.base_url,
        "model": route.model,
        "temperature": 0.0,
        "stream": False,
        "include_usage": True,
        "json_mode": route.json_mode,
        "provider_max_retries": route.retry_policy.max_retries,
        "provider_timeout_sec": route.timeout_ms / 1000,
        "cache_scope_sha256": _secret_fingerprint(route.api_key),
    }


def _attach_repository_context(
    state: RunState,
    workspace: Path,
    *,
    trace: TraceRecorder | None = None,
) -> None:
    profile = inspect_repository(workspace)
    state.repository_profile = profile.to_dict()
    state.project_guide = profile.guide.to_dict() if profile.guide is not None else {}
    state.metrics["repository_profile_schema_version"] = profile.schema_version
    state.metrics["repository_workspace_kind"] = profile.workspace_kind
    state.metrics["project_guide_status"] = profile.guide_status
    if profile.guide is not None:
        state.metrics["project_guide_sha256"] = profile.guide.sha256
    if state.run_dir is not None:
        profile_path = state.run_dir / "repository_profile.json"
        state.metrics["repository_profile_path"] = str(profile_path)
        state.metrics["repository_profile_sha256"] = write_repository_profile(profile, profile_path)
    if trace is not None:
        trace.record(
            "repository_profile_created",
            state,
            schema_version=profile.schema_version,
            workspace_kind=profile.workspace_kind,
            guide_status=profile.guide_status,
            build_files=list(profile.build_files),
            candidate_test_commands=list(profile.candidate_test_commands),
        )


def _build_loop(
    provider: OpenAICompatibleProvider,
    executor: BashExecutor,
    *,
    settings: Settings,
    session: SessionManager | None = None,
    state: RunState | None = None,
    stream: bool | None = None,
    interrupt_after_steps: int | None = None,
    completion_verifier: CompletionVerifier | None = None,
    memory_store: MemoryStore | None = None,
) -> AgentLoop:
    if state is not None:
        start_session = getattr(provider, "start_session", None)
        if callable(start_session):
            start_session(state.run_id)
    skill_workspace = state.workspace_host_path if state and state.workspace_host_path else Path.cwd()
    trace = TraceRecorder(trace_path_for(state)) if state is not None else TraceRecorder()
    if state is not None and state.repository_profile and not state.metrics.get(
        "repository_profile_trace_recorded"
    ):
        trace.record(
            "repository_profile_created",
            state,
            schema_version=state.metrics.get("repository_profile_schema_version", 1),
            workspace_kind=state.metrics.get("repository_workspace_kind", "unknown"),
            guide_status=state.metrics.get("project_guide_status", "absent"),
            build_files=state.repository_profile.get("build_files", []),
            candidate_test_commands=state.repository_profile.get("candidate_test_commands", []),
        )
        state.metrics["repository_profile_trace_recorded"] = 1
    checkpoint_manager = (
        CheckpointManager(state.run_dir, trace=trace)
        if state is not None and state.run_dir is not None and state.workspace_host_path is not None
        else None
    )
    semantic_compactor = None
    if settings.context.compaction_strategy == "semantic":
        compaction_provider = provider if settings.aux is None else _build_aux_provider(settings)
        semantic_compactor = SemanticCompactor(
            compaction_provider,
            trace=trace,
            max_input_chars=settings.context.semantic_max_input_chars,
            max_summary_chars=settings.context.summary_max_chars,
        )
    prompt_layout = settings.context.prompt_layout
    if state is not None and int(state.metrics.get("turns") or 0) > 0:
        stored_layout = state.metrics.get("prompt_layout")
        prompt_layout = (
            stored_layout
            if stored_layout in {"rebuild", "append", "epoch", "append_until_compaction"}
            else "rebuild"
        )
    feedback_memory = (
        None
        if state is not None
        and state.prompt_namespace.startswith(("cache-experiment/", "memory-experiment/"))
        else FeedbackMemory(Path.cwd() / ".minicc" / "memory" / "feedback_rules.jsonl")
    )
    if state is not None and state.metrics.get("guidance_variant") == "a0":
        skill_registry = None
        feedback_memory = None
    else:
        skill_registry = SkillRegistry(roots=default_skill_roots(skill_workspace))
        if (
            state is not None
            and state.workspace_host_path is not None
            and state.metrics.get("guidance_feedback_path")
        ):
            feedback_memory = FeedbackMemory(
                state.workspace_host_path / str(state.metrics["guidance_feedback_path"])
            )
    profile = settings.tooling.profile
    scheduler = (
        ToolCallScheduler(
            HybridToolRunner(executor),
            max_parallel_tool_calls=settings.tooling.max_parallel_tool_calls,
        )
        if profile == "hybrid-v3.6"
        else None
    )
    if state is not None:
        state.metrics["profile"] = profile
        state.metrics["max_parallel_tool_calls"] = settings.tooling.max_parallel_tool_calls
        state.metrics["child_model"] = _child_model(settings) if profile == "multi-agent-v4" else None
    workflow_coordinator = None
    if profile == "multi-agent-v4":
        child_route = _child_route(settings)
        workflow_coordinator = WorkflowCoordinator(
            SubprocessChildRunProvider(timeout_sec=child_route.timeout_ms / 1000),
            max_concurrent_children=settings.tooling.max_parallel_tool_calls,
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
                context_window=settings.default_route.context_window,
                threshold_ratio=settings.context.threshold_ratio,
                retain_ratio=settings.context.retain_ratio,
                max_overflow_retries=settings.context.max_overflow_retries,
            ),
            skill_registry=skill_registry,
            feedback_memory=feedback_memory,
            semantic_compactor=semantic_compactor,
            memory_store=memory_store,
            memory_max_results=settings.memory.max_results,
            memory_max_chars_per_memory=settings.memory.max_chars_per_memory,
            memory_max_total_chars=settings.memory.max_total_chars,
        ),
        policy_chain=build_policy_chain(settings),
        session=session,
        trace=trace,
        checkpoint_manager=checkpoint_manager,
        completion_verifier=completion_verifier,
        tool_scheduler=scheduler,
        workflow_coordinator=workflow_coordinator,
        turn_provider_factory=lambda runner: _build_turn_provider(runner, provider, settings),
        config=LoopConfig(
            max_seconds=settings.budget.max_seconds,
            max_turns=settings.budget.max_turns,
            max_action_timeout_sec=settings.budget.max_action_timeout_sec,
            model_options=CompletionOptions(
                temperature=0.0,
                stream=False if stream is None else stream,
                include_usage=True,
                json_mode=settings.default_route.json_mode,
            ),
            interrupt_after_steps=interrupt_after_steps,
            profile=profile,
            max_parallel_tool_calls=settings.tooling.max_parallel_tool_calls,
            max_tool_calls_per_step=settings.tooling.max_tool_calls_per_step,
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
        strategy: CompactionStrategy = "semantic" if context_variant == "a1" else "disabled"
        settings = replace(settings, context=replace(settings.context, compaction_strategy=strategy))
    cache_variant = getattr(args, "cache_variant", None)
    cache_sequence_id = str(getattr(args, "cache_sequence_id", "") or "").strip()
    execution_order = getattr(args, "execution_order", None)
    guidance_variant = getattr(args, "guidance_variant", None)
    guidance_sequence_id = str(getattr(args, "guidance_sequence_id", "") or "").strip()
    guidance_execution_order = getattr(args, "guidance_execution_order", None)
    guidance_feedback_path = str(
        getattr(args, "guidance_feedback_path", "guidance/feedback_rules.jsonl") or ""
    ).strip().replace("\\", "/")
    if guidance_variant and (context_variant or cache_variant):
        print("--guidance-variant cannot be combined with context/cache variants.", file=sys.stderr)
        return 2
    if guidance_variant and not guidance_sequence_id:
        print("--guidance-variant requires --guidance-sequence-id.", file=sys.stderr)
        return 2
    if guidance_variant and not guidance_execution_order:
        print("--guidance-variant requires --guidance-execution-order.", file=sys.stderr)
        return 2
    if (guidance_sequence_id or guidance_execution_order) and not guidance_variant:
        print("guidance sequence/order options require --guidance-variant.", file=sys.stderr)
        return 2
    if guidance_variant and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", guidance_sequence_id):
        print("invalid --guidance-sequence-id.", file=sys.stderr)
        return 2
    feedback_parts = PurePosixPath(guidance_feedback_path).parts
    if guidance_variant and (
        not guidance_feedback_path
        or PurePosixPath(guidance_feedback_path).is_absolute()
        or ".." in feedback_parts
    ):
        print("--guidance-feedback-path must be a safe workspace-relative path.", file=sys.stderr)
        return 2
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
        prompt_layout = cast(
            PromptLayout,
            {"p0": "rebuild", "p1": "append", "p2": "epoch"}[cache_variant],
        )
        settings = replace(settings, context=replace(settings.context, prompt_layout=prompt_layout))
    guidance_namespace = (
        f"guidance-experiment/{guidance_sequence_id}/{guidance_variant}"
        if guidance_variant
        else ""
    )
    milestone = _effective_milestone(settings, args)
    v212_formal = bool(args.release_gate and "2.1.2" in milestone)
    v30_formal = bool(args.release_gate and "3.0" in milestone)
    v32_formal = bool(args.release_gate and milestone == "v3.2-guidance-acceptance")
    catalog = RunCatalog(Path.cwd() / ".minicc" / "versions")
    git_commit, worktree_dirty = _git_evidence(Path.cwd())
    selected_case_names = set(args.case_names or [])
    discovered_cases = discover_cases(Path(args.path))
    discovered_names = {case.name for case in discovered_cases}
    missing_cases = sorted(selected_case_names - discovered_names)
    if missing_cases:
        print(f"Unknown eval case(s): {', '.join(missing_cases)}", file=sys.stderr)
        return 2
    selected_cases = [
        case
        for case in discovered_cases
        if not selected_case_names or case.name in selected_case_names
    ]
    if v212_formal and len(selected_cases) != 2:
        print(
            "Release gate rejected: V2.1.2 requires one canonical definition "
            "for each C02/C07 case",
            file=sys.stderr,
        )
        return 2
    if v30_formal and len(selected_cases) != 5:
        print(
            "Release gate rejected: V3.0 requires one canonical definition "
            "for each C01/C02/C03/C04/C09 case",
            file=sys.stderr,
        )
        return 2
    if v32_formal and (
        len(selected_cases) != 1 or selected_cases[0].name != "G01_release_manifest_guidance"
    ):
        print("Release gate rejected: V3.2 requires the canonical G01 case", file=sys.stderr)
        return 2
    git_preflight_verified = False
    if v212_formal or v30_formal or v32_formal:
        formal_git_error = _git_formal_state_error(Path.cwd())
        if formal_git_error:
            print(
                f"Release gate rejected: {formal_git_error}",
                file=sys.stderr,
            )
            return 2
        git_preflight_verified = True
    if args.release_gate:
        gate_error = _release_gate_error(
            args,
            git_commit,
            worktree_dirty,
            settings.sandbox.image,
            milestone=milestone,
        )
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
        if guidance_namespace:
            state.prompt_namespace = guidance_namespace
            state.metrics["guidance_variant"] = guidance_variant
            state.metrics["guidance_sequence_id"] = guidance_sequence_id
            state.metrics["guidance_feedback_path"] = guidance_feedback_path
        if state.run_dir is None or state.artifacts_dir is None or state.workspace_host_path is None:
            raise RuntimeError("eval runner did not initialize run workspace paths")
        _attach_repository_context(state, state.workspace_host_path)
        artifacts = ArtifactStore(
            state.artifacts_dir,
            display_path_prefix=".minicc_artifacts",
            preview_chars=settings.context.artifact_preview_chars,
        )
        runner = None
        try:
            if args.execute_local or case.sandbox_mode == "local":
                executor: BashExecutor = LocalCommandExecutor(
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
                completion_verifier=_completion_verifier_for_case(case),
            )
            return loop.run(state).state
        finally:
            if runner is not None:
                runner.cleanup(state.container_name)
            close_provider = getattr(provider, "close", None)
            if callable(close_provider):
                close_provider()

    case_contexts = {
        case.name: dict(case.context)
        for case in selected_cases
    }
    configuration = {
        **_provider_summary(settings),
        "sandbox_mode": settings.sandbox.mode,
        "execute_local": bool(args.execute_local),
        "docker_image": settings.sandbox.image,
        "git_commit": git_commit,
        "worktree_dirty": worktree_dirty,
        "release_gate": bool(args.release_gate),
        "milestone": milestone,
        "context_variant": context_variant or "configured",
        "cache_variant": cache_variant or "configured",
        "cache_sequence_id": cache_sequence_id or None,
        "execution_order": execution_order,
        "guidance_variant": guidance_variant or "configured",
        "guidance_sequence_id": guidance_sequence_id or None,
        "guidance_execution_order": guidance_execution_order,
        "guidance_feedback_path": guidance_feedback_path if guidance_variant else None,
        "feedback_memory_mode": (
            "disabled"
            if cache_variant or guidance_variant == "a0"
            else "commit_bound" if guidance_variant == "a1" else "configured"
        ),
        "prompt_layout": settings.context.prompt_layout,
        "compaction_strategy": settings.context.compaction_strategy,
        "system_prefix_sha256": hashlib.sha256(STABLE_PREFIX.encode("utf-8")).hexdigest(),
        "max_prompt_chars": settings.context.max_prompt_chars,
        "recent_turns": settings.context.recent_turns,
        "case_contexts": case_contexts,
        "git_preflight_verified": git_preflight_verified,
        "git_postflight_verified": False,
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
    if args.release_gate:
        post_git_commit, post_worktree_dirty = _git_evidence(Path.cwd())
        post_formal_git_error = (
            _git_formal_state_error(Path.cwd())
            if (v212_formal or v30_formal or v32_formal)
            else ""
        )
        if (
            post_git_commit != git_commit
            or post_worktree_dirty
            or bool(post_formal_git_error)
        ):
            print(
                "Release gate rejected: Git state changed during execution",
                file=sys.stderr,
            )
            return 2
        if isinstance(result.configuration, dict):
            result.configuration["git_postflight_verified"] = True
        for case_result in result.cases:
            if isinstance(case_result.configuration, dict):
                case_result.configuration["git_postflight_verified"] = True
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


def memory_eval_command(args: argparse.Namespace) -> int:
    if args.repeat < 1:
        print("--repeat must be at least 1.", file=sys.stderr)
        return 2
    settings = load_settings()
    milestone = _effective_milestone(settings, args)
    try:
        case = load_follow_up_case(Path(args.path))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    project_root = Path.cwd()
    git_commit, worktree_dirty = _git_evidence(project_root)
    release_gate = bool(getattr(args, "release_gate", False))
    case_name = case.source.name.removesuffix("_source")
    git_preflight_verified = False
    if release_gate:
        gate_error = _memory_release_gate_error(
            args,
            git_commit,
            worktree_dirty,
            settings.sandbox.image,
            milestone=milestone,
            case_name=case_name,
        )
        if gate_error:
            print(f"Release gate rejected: {gate_error}", file=sys.stderr)
            return 2
        formal_git_error = _git_formal_state_error(project_root)
        if formal_git_error:
            print(f"Release gate rejected: {formal_git_error}", file=sys.stderr)
            return 2
        git_preflight_verified = True
    provider = _build_provider_or_print_error(settings)
    if provider is None:
        return 2
    suite_id = new_suite_id()
    runs_root = Path.cwd() / ".minicc" / "runs"
    suites_root = Path.cwd() / ".minicc" / "suites"
    catalog = RunCatalog(Path.cwd() / ".minicc" / "versions")

    def agent_runner(eval_case: EvalCase, state: RunState, source_run_id: str | None) -> RunState:
        state.constraints.extend(_case_constraints(eval_case))
        state.prompt_namespace = f"memory-experiment/{suite_id}"
        if source_run_id is not None:
            attach_working_memory(state, source_run_id, runs_root=runs_root)
        if state.run_dir is None or state.artifacts_dir is None or state.workspace_host_path is None:
            raise RuntimeError("memory eval runner did not initialize run workspace paths")
        _attach_repository_context(state, state.workspace_host_path)
        artifacts = ArtifactStore(
            state.artifacts_dir,
            display_path_prefix=".minicc_artifacts",
            preview_chars=settings.context.artifact_preview_chars,
        )
        runner = None
        try:
            if args.execute_local or eval_case.sandbox_mode == "local":
                executor: BashExecutor = LocalCommandExecutor(
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
                        network=(
                            "bridge" if eval_case.sandbox_mode == "dev" else settings.sandbox.network
                        ),
                    )
                )
                state.container_name = runner.start(
                    run_id=state.run_id,
                    workspace_dir=state.workspace_host_path,
                    artifacts_dir=state.artifacts_dir,
                    writable_paths=eval_case.writable_paths,
                )
                executor = DockerCommandExecutor(runner, artifacts=artifacts)
            session = SessionManager()
            session.save(state)
            case_settings = _settings_for_eval_case(settings, eval_case)
            loop = _build_loop(
                provider,
                executor,
                settings=case_settings,
                session=session,
                state=state,
                completion_verifier=_completion_verifier_for_case(eval_case),
            )
            return loop.run(state).state
        finally:
            if runner is not None:
                runner.cleanup(state.container_name)

    configuration = {
        "command": (
            f"minicc memory-eval {Path(args.path).as_posix()} --repeat {args.repeat} "
            f"--execution-order {args.execution_order}"
            + (" --execute-local" if args.execute_local else "")
            + (f" --milestone {milestone} --release-gate" if release_gate else "")
        ),
        "base_url": settings.default_route.base_url,
        "model": settings.default_route.model,
        "temperature": 0.0,
        "provider_timeout_sec": settings.default_route.timeout_ms / 1000,
        "provider_max_retries": settings.default_route.retry_policy.max_retries,
        "sandbox_mode": "local" if args.execute_local else settings.sandbox.mode,
        "docker_image": settings.sandbox.image,
        "git_commit": git_commit,
        "worktree_dirty": worktree_dirty,
        "release_gate": release_gate,
        "case_name": case_name,
        "git_preflight_verified": git_preflight_verified,
        "git_postflight_verified": False,
        "feedback_memory_mode": "disabled",
        "working_memory_mode": "explicit_source_run",
        "prompt_layout": settings.context.prompt_layout,
        "compaction_strategy": settings.context.compaction_strategy,
        "expected_memory_paths": list(case.expected_memory_paths),
    }
    result = None
    bundle = None
    try:
        result = run_memory_ab(
            case,
            runs_root=runs_root,
            agent_runner=agent_runner,
            repeat=args.repeat,
            execution_order=args.execution_order,
            configuration=configuration,
            milestone=milestone,
            stage="formal_acceptance" if release_gate else "development_precheck",
            suite_id=suite_id,
        )
        if release_gate:
            postflight_error = _git_postflight_error(project_root, expected_commit=git_commit)
            if postflight_error:
                print(f"Release gate rejected: {postflight_error}", file=sys.stderr)
                return 2
            result.configuration["git_postflight_verified"] = True
            for case_result in result.case_results:
                if isinstance(case_result.configuration, dict):
                    case_result.configuration["git_postflight_verified"] = True
        bundle = write_memory_ab_report(result, suites_root)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Memory A/B failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close_provider = getattr(provider, "close", None)
        if callable(close_provider):
            close_provider()

    if result is None or bundle is None:
        return 1
    catalog_entries = [
        catalog.register_eval_result(
            milestone,
            case_result,
            stage=result.stage,
            source="memory-eval",
            git_commit=git_commit,
            report_path=str(bundle.report_json_path),
            suite_path=str(bundle.manifest_path),
        )
        for case_result in result.case_results
    ]
    ledger_complete = all(entry is not None for entry in catalog_entries)
    print(f"memory_ab_result: {'PASS' if result.passed else 'FAIL'}")
    print(f"ledger_status: {'COMPLETE' if ledger_complete else 'INCOMPLETE'}")
    print(f"suite_id: {result.suite_id}")
    print(f"report_json: {bundle.report_json_path}")
    print(f"report_markdown: {bundle.report_markdown_path}")
    return 0 if result.passed and ledger_complete else 1


def memory_report_command(args: argparse.Namespace) -> int:
    if len(args.report) != 3:
        print("memory-report requires exactly three --report inputs.", file=sys.stderr)
        return 2
    project_root = Path.cwd()
    git_commit, worktree_dirty = _git_evidence(project_root)
    if worktree_dirty:
        print("Memory acceptance requires a clean Git worktree before writing.", file=sys.stderr)
        return 2
    expected_output = (project_root / "acceptance" / "stable-v2.2").resolve()
    if args.output_dir.resolve() != expected_output:
        print("Memory acceptance output must be acceptance/stable-v2.2.", file=sys.stderr)
        return 2
    try:
        suites = [load_memory_suite_report(path, verify_manifest=True) for path in args.report]
        report = build_memory_acceptance_report(suites)
        source_commit = str((report.get("locked_configuration") or {}).get("git_commit") or "")
        if source_commit != git_commit:
            raise ValueError("formal memory suites are not bound to the current Git commit")
        bundle = write_memory_acceptance_report(report, args.output_dir)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"Cannot build V2.2 memory acceptance report: {exc}", file=sys.stderr)
        return 1
    print(f"memory_acceptance_status: {report['status']}")
    print(f"report_json: {bundle.json_path}")
    print(f"report_markdown: {bundle.markdown_path}")
    print(f"evidence_json: {bundle.evidence_path}")
    print(f"manifest_json: {bundle.manifest_path}")
    return 0


def release_report_command(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    source_commit, worktree_dirty = _git_evidence(project_root)
    output_dir = args.output_dir or (
        Path.cwd() / ".minicc" / "release-reports" / new_suite_id().replace("suite-", "release-")
    )
    try:
        loader = _optional_release_evidence if args.allow_missing else load_json_evidence
        system_report = loader(args.system_report)
        context_report = loader(args.context_report)
        memory_report = loader(args.memory_report)
        resume_report = loader(args.resume_report)
        if context_report.get("rounds"):
            try:
                context_suites = load_context_suite_evidence(
                    context_report,
                    suites_root=args.suites_root,
                )
            except (OSError, ValueError):
                if not args.allow_missing:
                    raise
                context_suites = []
        else:
            context_suites = []
        if args.release_gate:
            gate_error = _release_report_gate_error(
                project_root=project_root,
                source_commit=source_commit,
                worktree_dirty=worktree_dirty,
                output_dir=output_dir,
                system_report=system_report,
            )
            if gate_error:
                print(f"V3.0 release report rejected: {gate_error}", file=sys.stderr)
                return 2
        report = build_release_report(
            system_report=system_report,
            context_report=context_report,
            context_suites=context_suites,
            memory_report=memory_report,
            resume_report=resume_report,
            source_commit=source_commit,
        )
        if args.release_gate:
            report["milestone"] = "stable-v3.0"
            report["formal_release_gate"] = True
            execution_commit = str(
                (system_report.get("configuration") or {}).get("git_commit") or ""
            )
            report["execution_commit"] = execution_commit
            report["verification_commit"] = source_commit
            report["verification_delta_paths"] = _git_changed_paths(
                project_root,
                execution_commit,
                source_commit,
            )
            if report.get("passed") is not True:
                raise ValueError("formal V3.0 report did not pass all four dimensions")
        bundle = write_release_report(report, output_dir)
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"Cannot build V3.0 release report: {exc}", file=sys.stderr)
        return 1
    print(f"release_report_status: {report['status']}")
    print(f"report_json: {bundle.json_path}")
    print(f"report_markdown: {bundle.markdown_path}")
    print(f"report_csv: {bundle.csv_path}")
    print(f"manifest_json: {bundle.manifest_path}")
    return 0 if report["passed"] else 1


def _optional_release_evidence(path: Path) -> dict:
    return load_json_evidence(path) if path.is_file() else {}


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
        gate_error = _cache_probe_release_gate_error(
            args,
            git_commit,
            worktree_dirty,
            milestone=milestone,
        )
        if gate_error:
            print(f"Cache probe release gate rejected: {gate_error}", file=sys.stderr)
            return 2
    git_preflight_verified = False
    if args.release_gate and "2.1.2" in milestone:
        formal_git_error = _git_formal_state_error(Path.cwd())
        if formal_git_error:
            print(
                f"Cache probe release gate rejected: {formal_git_error}",
                file=sys.stderr,
            )
            return 2
        git_preflight_verified = True
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
    prompt_layout = cast(
        PromptLayout,
        {"p0": "rebuild", "p1": "append", "p2": "epoch"}[args.cache_variant],
    )
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
        **_provider_summary(settings),
        "git_commit": git_commit,
        "worktree_dirty": worktree_dirty,
        "release_gate": bool(args.release_gate),
        "milestone": milestone,
        "compaction_strategy": settings.context.compaction_strategy,
        "recent_turns": settings.context.recent_turns,
        "max_prompt_chars": settings.context.max_prompt_chars,
        "execution_order": args.execution_order,
        "feedback_memory_mode": "disabled",
        "git_preflight_verified": git_preflight_verified,
        "git_postflight_verified": False,
    }
    postflight_check: Callable[[], str | None] | None = None
    if git_preflight_verified:
        def check_git_postflight() -> str | None:
            return _git_postflight_error(
                Path.cwd(),
                expected_commit=git_commit,
            )

        postflight_check = check_git_postflight
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
                    temperature=0.0,
                    stream=False,
                    include_usage=True,
                    json_mode=settings.default_route.json_mode,
                ),
                configuration=configuration,
                milestone=milestone,
                stage="formal_acceptance" if args.release_gate else "development_precheck",
                postflight_check=postflight_check,
            ),
        )
        report = load_cache_probe_report(
            bundle.report_json_path,
            verify_manifest=True,
        )
    except (OSError, ValueError) as exc:
        print(f"Cannot run cache probe: {exc}", file=sys.stderr)
        return 2
    finally:
        close_provider = getattr(provider, "close", None)
        if callable(close_provider):
            close_provider()

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


def cache_utilization_report_command(args: argparse.Namespace) -> int:
    lengths = {
        len(args.p1_probe),
        len(args.p2_probe),
        len(args.p1_eval),
        len(args.p2_eval),
    }
    if lengths != {2}:
        print(
            "cache-utilization-report requires exactly two of every P1/P2 probe/eval report.",
            file=sys.stderr,
        )
        return 2
    try:
        rounds = [
            (
                load_cache_probe_report(p1_probe, verify_manifest=True),
                load_cache_probe_report(p2_probe, verify_manifest=True),
                load_cache_suite_report(p1_eval, verify_manifest=True),
                load_cache_suite_report(p2_eval, verify_manifest=True),
            )
            for p1_probe, p2_probe, p1_eval, p2_eval in zip(
                args.p1_probe,
                args.p2_probe,
                args.p1_eval,
                args.p2_eval,
                strict=True,
            )
        ]
        report = build_cache_utilization_report(rounds)
        if not report["passed"]:
            print("cache_utilization_status: FAIL")
            for criterion in failed_cache_utilization_criteria(report):
                print(f"failed_criterion: {criterion}", file=sys.stderr)
            print(
                "V2.1.2 cache utilization did not pass; no acceptance report was written.",
                file=sys.stderr,
            )
            return 1
        bundle = write_cache_utilization_report(report, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"Cannot build V2.1.2 cache utilization report: {exc}", file=sys.stderr)
        return 2
    print(f"cache_utilization_status: {report['status']}")
    print(f"json_report: {bundle.json_path}")
    print(f"markdown_report: {bundle.markdown_path}")
    print(f"evidence_bundle: {bundle.evidence_path}")
    print(f"manifest: {bundle.manifest_path}")
    return 0


def _settings_for_eval_case(settings: Settings, case: EvalCase) -> Settings:
    budget = settings.budget
    if case.budget:
        budget = BudgetSettings(
            max_bash_actions=_case_int(case, "max_bash_actions", settings.budget.max_bash_actions),
            max_seconds=_case_int(case, "max_seconds", settings.budget.max_seconds),
            max_turns=_case_int(case, "max_turns", settings.budget.max_turns),
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
        artifact_preview_chars=_context_int(
            case,
            "artifact_preview_chars",
            settings.context.artifact_preview_chars,
        ),
        summary_max_chars=_context_int(case, "summary_max_chars", settings.context.summary_max_chars),
        field_preview_chars=_context_int(
            case,
            "field_preview_chars",
            settings.context.field_preview_chars,
        ),
        retention_markers=(
            tuple(str(item) for item in case.context.get("retention_markers", []))
            or settings.context.retention_markers
        ),
        threshold_ratio=_context_float(case, "threshold_ratio", settings.context.threshold_ratio),
        retain_ratio=_context_float(case, "retain_ratio", settings.context.retain_ratio),
        max_overflow_retries=_context_int(
            case,
            "max_overflow_retries",
            settings.context.max_overflow_retries,
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
            providers=settings.providers,
            default_provider=settings.default_provider,
            failover=settings.failover,
            child=settings.child,
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
        providers=settings.providers,
        default_provider=settings.default_provider,
        failover=settings.failover,
        child=settings.child,
        sandbox=sandbox,
        budget=budget,
        context=context,
        policy=case_policy,
        project=settings.project,
        workspace=settings.workspace,
    )


def _case_int(case: EvalCase, name: str, default: int) -> int:
    value = case.budget.get(name)
    if value is None or isinstance(value, bool):
        return default
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _context_int(case: EvalCase, name: str, default: int) -> int:
    value = case.context.get(name)
    if value is None or isinstance(value, bool):
        return default
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _context_float(case: EvalCase, name: str, default: float) -> float:
    value = case.context.get(name)
    if value is None or isinstance(value, bool):
        return default
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return default
    try:
        return float(value)
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
    if case.initial_verify is not None and case.initial_verify.get("command"):
        verification_commands.append(str(case.initial_verify["command"]))
    if verification_commands:
        constraints.append(
            "Use these authoritative offline verification commands; do not install a different test runner: "
            + " ; ".join(verification_commands)
        )
    return constraints


def _completion_verifier_for_case(case: EvalCase) -> CompletionVerifier | None:
    if not case.completion_gate:
        return None
    commands = tuple(
        str(assertion.get("command"))
        for assertion in case.assertions
        if assertion.get("type") == "command" and assertion.get("command")
    )
    if not commands and case.initial_verify is not None and case.initial_verify.get("command"):
        commands = (str(case.initial_verify["command"]),)
    if not commands:
        raise ValueError(f"completion_gate requires an authoritative verification command: {case.name}")
    return CommandCompletionVerifier(
        commands=commands,
        timeout_sec=_case_int(case, "verification_timeout_sec", 120),
    )


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


def _git_index_flags_error(cwd: Path) -> str:
    """Reject index flags that can hide tracked-file changes from Git status."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-v", "-z"],
            cwd=cwd,
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "cannot inspect Git skip-worktree/assume-unchanged flags"
    flagged: list[str] = []
    for raw_record in result.stdout.split(b"\0"):
        if not raw_record:
            continue
        tag = chr(raw_record[0])
        if tag != "S" and not tag.islower():
            continue
        raw_path = (
            raw_record[2:]
            if len(raw_record) > 1 and raw_record[1:2] == b" "
            else raw_record[1:]
        )
        path = raw_path.decode("utf-8", errors="replace")
        flag = "skip-worktree" if tag.upper() == "S" else "assume-unchanged"
        flagged.append(f"{flag}:{path}")
    if not flagged:
        return ""
    preview = ", ".join(flagged[:5])
    if len(flagged) > 5:
        preview += f", ... ({len(flagged)} total)"
    return f"Git index contains hidden-change flags: {preview}"


def _git_transform_attributes_error(cwd: Path) -> str:
    """Reject ambient attributes that can transform committed content."""
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=cwd,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout
        result = subprocess.run(
            [
                "git",
                "check-attr",
                "-z",
                "--stdin",
                "filter",
                "ident",
                "working-tree-encoding",
            ],
            cwd=cwd,
            input=tracked,
            capture_output=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "cannot inspect Git content-transform attributes"
    fields = result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        return "Git content-transform attribute output is malformed"
    transformed: list[str] = []
    for index in range(0, len(fields), 3):
        raw_path, raw_attribute, raw_value = fields[index : index + 3]
        if raw_value == b"unspecified":
            continue
        path = raw_path.decode("utf-8", errors="replace")
        attribute = raw_attribute.decode("ascii", errors="replace")
        value = raw_value.decode("utf-8", errors="replace")
        transformed.append(f"{path}:{attribute}={value}")
    if not transformed:
        return ""
    preview = ", ".join(transformed[:5])
    if len(transformed) > 5:
        preview += f", ... ({len(transformed)} total)"
    return f"Git content-transform attributes are not allowed: {preview}"


def _git_formal_state_error(cwd: Path) -> str:
    return _git_index_flags_error(cwd) or _git_transform_attributes_error(cwd)


def _git_postflight_error(cwd: Path, *, expected_commit: str) -> str:
    post_git_commit, post_worktree_dirty = _git_evidence(cwd)
    if post_git_commit != expected_commit:
        return "Git commit changed during execution"
    if post_worktree_dirty:
        return "Git worktree changed during execution"
    return _git_formal_state_error(cwd)


def _release_gate_error(
    args: argparse.Namespace,
    git_commit: str,
    worktree_dirty: bool,
    docker_image: str = "python:test@sha256:test",
    *,
    milestone: str = "",
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
    if "2.1.2" in milestone:
        if args.repeat != 3:
            return "V2.1.2 acceptance requires exactly --repeat 3"
        expected_path = (Path.cwd() / "eval_cases" / "capability_suite_v1").resolve()
        if Path(args.path).resolve() != expected_path:
            return "V2.1.2 acceptance requires eval_cases/capability_suite_v1"
        expected_cases = {
            "C02_fix_failing_test",
            "C07_large_log_debugging",
        }
        if set(args.case_names) != expected_cases:
            return "V2.1.2 acceptance requires the exact C02/C07 case matrix"
    if "3.0" in milestone:
        if args.repeat != 3:
            return "V3.0 acceptance requires exactly --repeat 3"
        expected_path = (Path.cwd() / "eval_cases" / "capability_suite_v1").resolve()
        if Path(args.path).resolve() != expected_path:
            return "V3.0 acceptance requires eval_cases/capability_suite_v1"
        expected_cases = {
            "C01_repo_onboarding",
            "C02_fix_failing_test",
            "C03_add_cli_option",
            "C04_add_regression_test",
            "C09_hitl_destructive_command",
        }
        if set(args.case_names) != expected_cases:
            return "V3.0 acceptance requires the exact C01/C02/C03/C04/C09 matrix"
    if milestone == "v3.2-guidance-acceptance":
        if args.repeat != 3:
            return "V3.2 guidance acceptance requires exactly --repeat 3"
        expected_path = (Path.cwd() / "eval_cases" / "guidance_suite_v1").resolve()
        if Path(args.path).resolve() != expected_path:
            return "V3.2 guidance acceptance requires eval_cases/guidance_suite_v1"
        if set(args.case_names) != {"G01_release_manifest_guidance"}:
            return "V3.2 guidance acceptance requires the canonical G01 case"
        if getattr(args, "guidance_variant", None) not in {"a0", "a1"}:
            return "V3.2 guidance acceptance requires --guidance-variant"
        if not getattr(args, "guidance_sequence_id", None):
            return "V3.2 guidance acceptance requires --guidance-sequence-id"
        if not getattr(args, "guidance_execution_order", None):
            return "V3.2 guidance acceptance requires --guidance-execution-order"
        if getattr(args, "guidance_feedback_path", None) != "guidance/feedback_rules.jsonl":
            return "V3.2 guidance acceptance requires the canonical feedback path"
    return ""


def _cache_probe_release_gate_error(
    args: argparse.Namespace,
    git_commit: str,
    worktree_dirty: bool,
    *,
    milestone: str = "",
) -> str:
    if not git_commit:
        return "the workspace is not pinned to a Git commit"
    if worktree_dirty:
        return "the Git worktree has uncommitted changes"
    expected_repeat = 12 if "2.1.2" in milestone else 5
    if args.repeat != expected_repeat:
        return f"formal cache probes require exactly --repeat {expected_repeat}"
    if not getattr(args, "execution_order", None):
        return "formal cache probes require --execution-order"
    return ""


def _memory_release_gate_error(
    args: argparse.Namespace,
    git_commit: str,
    worktree_dirty: bool,
    docker_image: str,
    *,
    milestone: str,
    case_name: str,
) -> str:
    if not git_commit:
        return "the workspace is not pinned to a Git commit"
    if worktree_dirty:
        return "the Git worktree has uncommitted changes"
    if args.execute_local:
        return "V2.2 acceptance must use Docker"
    if "@sha256:" not in docker_image:
        return "V2.2 acceptance requires a Docker image pinned by sha256 digest"
    if args.repeat != 3:
        return "V2.2 acceptance requires exactly --repeat 3"
    if args.execution_order != "alternating":
        return "V2.2 acceptance requires --execution-order alternating"
    if milestone != "v2.2-acceptance":
        return "V2.2 acceptance requires --milestone v2.2-acceptance"
    expected = REQUIRED_MEMORY_CASES.get(case_name)
    if expected is None:
        return "V2.2 acceptance requires one of the canonical M01/M02/M03 cases"
    case_path = Path(args.path).resolve()
    if case_path.is_dir():
        case_path = case_path / "case.yaml"
    if case_path != (Path.cwd() / expected[0]).resolve():
        return f"V2.2 acceptance requires canonical case path for {case_name}"
    return ""


def _release_report_gate_error(
    *,
    project_root: Path,
    source_commit: str,
    worktree_dirty: bool,
    output_dir: Path,
    system_report: dict,
) -> str:
    if not source_commit:
        return "the workspace is not pinned to a Git commit"
    if worktree_dirty:
        return "the Git worktree has uncommitted changes"
    formal_git_error = _git_formal_state_error(project_root)
    if formal_git_error:
        return formal_git_error
    if output_dir.resolve() != (project_root / "acceptance" / "stable-v3.0").resolve():
        return "formal output must be acceptance/stable-v3.0"
    configuration = system_report.get("configuration") or {}
    expected_cases = {
        "C01_repo_onboarding",
        "C02_fix_failing_test",
        "C03_add_cli_option",
        "C04_add_regression_test",
        "C09_hitl_destructive_command",
    }
    cases = [row for row in system_report.get("cases", []) if isinstance(row, dict)]
    case_names = {str(row.get("name") or "") for row in cases}
    run_ids = [str(row.get("run_id") or "") for row in cases]
    if (
        system_report.get("stage") != "formal_acceptance"
        or system_report.get("passed") is not True
        or int(system_report.get("repeat") or 0) != 3
    ):
        return "system benchmark must be a repeat-3 formal PASS suite"
    if case_names != expected_cases or len(cases) != 15:
        return "system benchmark must contain exactly C01/C02/C03/C04/C09 x3"
    if len(set(run_ids)) != 15 or any(not run_id for run_id in run_ids):
        return "system benchmark run IDs must be complete and unique"
    suite_id = str(system_report.get("suite_id") or "")
    if any(not _formal_system_case_eligible(row, suite_id=suite_id) for row in cases):
        return "every system benchmark run must be formally metric eligible"
    execution_commit = str(configuration.get("git_commit") or "")
    try:
        delta_paths = _git_changed_paths(project_root, execution_commit, source_commit)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return f"cannot bind execution and verification commits: {exc}"
    allowed_verifier_delta = {"src/minicc/cli.py", "tests/test_cli.py"}
    if execution_commit != source_commit and (
        not delta_paths or not set(delta_paths).issubset(allowed_verifier_delta)
    ):
        return "verification commit contains changes outside the formal report verifier"
    if (
        configuration.get("worktree_dirty") is not False
        or configuration.get("release_gate") is not True
        or configuration.get("git_preflight_verified") is not True
        or configuration.get("git_postflight_verified") is not True
        or "@sha256:" not in str(configuration.get("docker_image") or "")
    ):
        return "system benchmark configuration is not bound to the current formal commit"
    return ""


def _formal_system_case_eligible(row: dict, *, suite_id: str) -> bool:
    run_dir = Path(str(row.get("run_dir") or ""))
    if not run_dir.is_dir():
        return False
    inspection = inspect_run(run_dir)
    return bool(
        inspection.get("formal_metric_eligible") is True
        and inspection.get("run_id") == row.get("run_id")
        and inspection.get("suite_id") == suite_id
        and inspection.get("stage") == "formal_acceptance"
    )


def _git_changed_paths(project_root: Path, base_commit: str, head_commit: str) -> list[str]:
    if not base_commit or not head_commit:
        raise ValueError("execution and verification commits are required")
    if base_commit == head_commit:
        return []
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_commit, head_commit],
        cwd=project_root,
        capture_output=True,
        timeout=10,
        check=True,
    )
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", base_commit, head_commit, "--"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


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


def meta_review_command(args: argparse.Namespace) -> int:
    run_dir = Path.cwd() / ".minicc" / "runs" / str(args.run_id)
    output_root = args.output_root or Path.cwd() / ".minicc" / "meta-reviews"
    provider = None
    settings = load_settings()
    if not args.offline:
        provider = _build_aux_provider(settings)
    try:
        implementation_commit, _ = _git_evidence(Path.cwd())
        result = MetaReviewer(
            provider,
            model=_aux_model(settings),
            implementation_commit=implementation_commit,
        ).review_run(
            run_dir,
            output_root=output_root,
            offline=bool(args.offline),
        )
    except (MetaReviewError, FileExistsError) as exc:
        print(f"Meta review failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close_provider = getattr(provider, "close", None)
        if callable(close_provider):
            close_provider()
    print("meta_review_status: PASS")
    print(f"review_id: {result.review_id}")
    print(f"used_model: {str(result.used_model).lower()}")
    print(f"review_dir: {result.output_dir}")
    return 0


def meta_review_report_command(args: argparse.Namespace) -> int:
    git_commit, worktree_dirty = _git_evidence(Path.cwd())
    if args.release_gate and worktree_dirty:
        print("Release gate rejected: worktree must be clean", file=sys.stderr)
        return 2
    try:
        disabled = load_cache_suite_report(args.disabled_suite, verify_manifest=True)
        enabled = load_cache_suite_report(args.enabled_suite, verify_manifest=True)
        reviews = [load_meta_review(path, verify_manifest=True) for path in args.reviews]
        suite_commits = {
            str(suite.get("configuration", {}).get("git_commit") or "")
            for suite in (disabled, enabled)
        }
        if len(suite_commits) != 1 or not next(iter(suite_commits)):
            raise ValueError("both suites must be bound to one execution commit")
        source_commit = next(iter(suite_commits))
        review_commits = {
            str(review.get("implementation_commit") or "") for review in reviews
        }
        if len(review_commits) != 1 or not next(iter(review_commits)):
            raise ValueError("all reviews must be bound to one implementation commit")
        review_commit = next(iter(review_commits))
        execution_review_changed_paths = _git_changed_paths(
            Path.cwd(), source_commit, review_commit
        )
        review_verification_changed_paths = _git_changed_paths(
            Path.cwd(), review_commit, git_commit
        )
        allowed_changed_paths = {
            "README.md",
            "STABLE_V1_MILESTONE_ROADMAP.md",
            "acceptance/experimental-v3.1-meta-review/manifest.json",
            "acceptance/experimental-v3.1-meta-review/report.csv",
            "acceptance/experimental-v3.1-meta-review/report.json",
            "acceptance/experimental-v3.1-meta-review/report.md",
            "docs/ETCLOVG_CAPABILITY_MATRIX.md",
            "src/minicc/cli.py",
            "src/minicc/evals/cache_ab.py",
            "src/minicc/evals/meta_review_ab.py",
            "src/minicc/meta/reviewer.py",
            "tests/test_cache_ab.py",
            "tests/test_meta_review.py",
            "tests/test_meta_review_ab.py",
        }
        if args.release_gate:
            unexpected = sorted(
                (
                    set(execution_review_changed_paths)
                    | set(review_verification_changed_paths)
                )
                - allowed_changed_paths
            )
            if unexpected:
                raise ValueError(
                    "execution/review commit delta contains disallowed paths: "
                    + ", ".join(unexpected)
                )
            if any(review.get("invocation", {}).get("used_model") is not True for review in reviews):
                raise ValueError("formal Meta Review evidence must use the model")
        report = build_meta_review_ab_report(
            disabled,
            enabled,
            reviews,
            source_commit=source_commit,
            review_commit=review_commit,
            verification_commit=git_commit,
            execution_review_changed_paths=execution_review_changed_paths,
            review_verification_changed_paths=review_verification_changed_paths,
            allowed_verification_paths=sorted(allowed_changed_paths),
        )
        bundle = write_meta_review_ab_report(report, args.output_dir)
    except (OSError, ValueError, MetaReviewError, FileExistsError) as exc:
        print(f"Meta Review report failed: {exc}", file=sys.stderr)
        return 1
    print(f"meta_review_ab_status: {report['status']}")
    print(f"json_report: {bundle['report.json']}")
    print(f"markdown_report: {bundle['report.md']}")
    return 0 if report["passed"] else 1


def guidance_report_command(args: argparse.Namespace) -> int:
    git_commit, worktree_dirty = _git_evidence(Path.cwd())
    if args.release_gate and worktree_dirty:
        print("Release gate rejected: worktree must be clean", file=sys.stderr)
        return 2
    try:
        disabled = load_cache_suite_report(args.disabled_suite, verify_manifest=True)
        enabled = load_cache_suite_report(args.enabled_suite, verify_manifest=True)
        suite_commits = {
            str(suite.get("configuration", {}).get("git_commit") or "")
            for suite in (disabled, enabled)
        }
        if len(suite_commits) != 1 or not next(iter(suite_commits)):
            raise ValueError("both suites must be bound to one execution commit")
        source_commit = next(iter(suite_commits))
        execution_verification_changed_paths = _git_changed_paths(
            Path.cwd(), source_commit, git_commit
        )
        allowed_changed_paths = {
            "CHANGELOG.md",
            "README.md",
            "STABLE_V1_MILESTONE_ROADMAP.md",
            "docs/ETCLOVG_CAPABILITY_MATRIX.md",
            "minicc.yaml",
            "pyproject.toml",
            "src/minicc/__init__.py",
            "src/minicc/cli.py",
            "src/minicc/evals/guidance_ab.py",
            "tests/test_cli.py",
            "tests/test_guidance_ab.py",
            "uv.lock",
        }
        if args.release_gate:
            unexpected = sorted(
                set(execution_verification_changed_paths) - allowed_changed_paths
            )
            if unexpected:
                raise ValueError(
                    "execution/verification commit delta contains disallowed paths: "
                    + ", ".join(unexpected)
                )
        report = build_guidance_ab_report(
            disabled,
            enabled,
            source_commit=source_commit,
            verification_commit=git_commit,
            execution_verification_changed_paths=execution_verification_changed_paths,
            allowed_verification_paths=sorted(allowed_changed_paths),
        )
        bundle = write_guidance_ab_report(report, args.output_dir)
    except (OSError, ValueError, FileExistsError) as exc:
        print(f"Guidance report failed: {exc}", file=sys.stderr)
        return 1
    print(f"guidance_ab_status: {report['status']}")
    print(f"json_report: {bundle['report.json']}")
    print(f"markdown_report: {bundle['report.md']}")
    return 0 if report["passed"] else 1


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
