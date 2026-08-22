from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from minicc.core.context import (
    STABLE_PREFIX,
    ContextBuilder,
    ContextConfig,
    PromptLayout,
    state_snapshot_text,
)
from minicc.core.protocol import BashAction, ProtocolError, parse_action
from minicc.core.provider import CompletionOptions, ModelProvider, ProviderError
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.evals.cache_probe import CacheProbeBundle, write_cache_probe
from minicc.prompts.cache_probe import FIXED_PROBE_CONSTRAINTS, FIXED_PROBE_GOAL

FIXED_LONG_EVIDENCE_CHARS = 8_000
FIXED_LONG_EVIDENCE_SOURCE = "src/minicc/evals/cache_probe.py"
_SAFE_SEQUENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FIXED_PROBE_PROFILE_KEYS = (
    "fixed_probe_contract_version",
    "cache_variant",
    "prompt_layout",
    "cache_sequence_id",
    "compaction_strategy",
    "recent_turns",
    "max_prompt_chars",
    "fixed_probe_repeat",
    "fixed_probe_warmup_requests",
    "expected_request_sha256s",
    "dynamic_sequence_sha256",
    "system_prefix_sha256",
    "long_evidence_source",
    "long_evidence_chars",
    "long_evidence_sha256",
)


@dataclass(frozen=True)
class CacheProbeRunConfig:
    variant: str
    repeat: int
    cache_sequence_id: str
    context: ContextConfig
    model_options: CompletionOptions
    configuration: Mapping[str, Any]
    milestone: str = "v2.1.1"
    stage: str = "development_precheck"
    warmup_requests: int = 2
    postflight_check: Callable[[], str | None] | None = None


def run_fixed_cache_probe(
    provider: ModelProvider,
    *,
    probes_root: Path,
    config: CacheProbeRunConfig,
) -> CacheProbeBundle:
    started_at = datetime.now(UTC).isoformat()
    if config.variant not in {"p0", "p1", "p2"}:
        raise ValueError("cache probe variant must be p0, p1, or p2")
    if config.repeat < 1:
        raise ValueError("cache probe repeat must be at least 1")
    _validate_sequence_id(config.cache_sequence_id)
    if (
        config.variant == "p1"
        and config.context.recent_turns < config.repeat - 1
        and "2.1.2" not in config.milestone
    ):
        raise ValueError(
            "cache probe recent_turns must retain every fixed-sequence step"
        )
    expected_layout = {"p0": "rebuild", "p1": "append", "p2": "epoch"}[config.variant]
    if config.context.prompt_layout != expected_layout:
        raise ValueError(
            f"cache probe {config.variant} requires prompt_layout={expected_layout}"
        )

    state = RunState.start(
        FIXED_PROBE_GOAL,
        milestone=config.milestone,
        stage=config.stage,
        prompt_namespace=f"cache-experiment/{config.cache_sequence_id}",
    )
    constraints = _fixed_probe_constraints(config.repeat)
    state.constraints.extend(constraints)
    start_session = getattr(provider, "start_session", None)
    if callable(start_session):
        start_session(f"cache-probe/{config.cache_sequence_id}/{config.variant}")
    builder = ContextBuilder(config.context)
    trajectory: list[TrajectoryStep] = []
    requests: list[dict[str, Any]] = []
    previous_prompt_tokens: int | None = None
    previous_completion_tokens: int | None = None

    for request_index in range(1, config.repeat + 1):
        messages = builder.build_messages(state, trajectory)
        record = _request_record(
            request_index=request_index,
            messages=messages,
            prefix_profile=state.metrics.get("stable_prefix_profile"),
        )
        try:
            response = provider.complete(messages, options=config.model_options)
        except ProviderError as exc:
            record.update(
                {
                    "request_success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            protocol_error: str | None
            try:
                action = parse_action(response.text)
            except ProtocolError as exc:
                task_success = False
                protocol_error = f"ProtocolError: {exc}"
            else:
                task_success = isinstance(action, BashAction)
                protocol_error = (
                    None
                    if task_success
                    else f"ProbeActionError: expected bash, got {action.type}"
                )
            record.update(
                {
                    "request_success": True,
                    "task_success": task_success,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                        "cached_tokens": response.usage.cached_tokens,
                        "cache_hit_tokens": response.usage.cache_hit_tokens,
                        "cache_miss_tokens": response.usage.cache_miss_tokens,
                    },
                    "latency_ms": response.latency_ms,
                    "attempt_count": response.attempt_count,
                    "retry_reasons": list(response.retry_reasons),
                    "response_sha256": _sha256_text(response.text),
                    **_response_metadata(response.raw),
                }
            )
            cacheability = _provider_cacheability(
                record,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                hit_tokens=(
                    response.usage.cache_hit_tokens
                    if response.usage.cache_hit_tokens is not None
                    else response.usage.cached_tokens
                ),
                previous_prompt_tokens=previous_prompt_tokens,
                previous_completion_tokens=previous_completion_tokens,
            )
            record["cacheability"] = cacheability
            previous_prompt_tokens = response.usage.prompt_tokens
            previous_completion_tokens = response.usage.completion_tokens
            if protocol_error:
                record["error"] = protocol_error
        requests.append(record)

        if request_index < config.repeat:
            state.metrics["turns"] = request_index
            step = _fixed_step(request_index)
            state.last_observation = step.observation
            trajectory.append(
                TrajectoryStep(
                    action=step.action,
                    observation=step.observation,
                    state_snapshot=state_snapshot_text(state),
                )
            )

    expected_request_sha256s = fixed_probe_request_sha256s(
        config.variant,
        config.repeat,
        config.cache_sequence_id,
        recent_turns=config.context.recent_turns,
        max_prompt_chars=config.context.max_prompt_chars,
        compaction_strategy=config.context.compaction_strategy,
    )
    actual_request_sha256s = [
        str(request.get("request_sha256") or "")
        for request in requests
    ]
    if actual_request_sha256s != expected_request_sha256s:
        raise ValueError("fixed cache probe request sequence drifted during execution")
    long_evidence = _fixed_long_evidence() if config.repeat >= 12 else ""
    configuration = dict(config.configuration)
    configuration.update(
        {
            "fixed_probe_contract_version": 1,
            "cache_variant": config.variant,
            "prompt_layout": config.context.prompt_layout,
            "compaction_strategy": config.context.compaction_strategy,
            "recent_turns": config.context.recent_turns,
            "max_prompt_chars": config.context.max_prompt_chars,
            "system_prefix_sha256": _sha256_text(STABLE_PREFIX),
            "dynamic_sequence_sha256": fixed_probe_sequence_sha256(
                config.repeat,
                config.cache_sequence_id,
            ),
            "cache_sequence_id": config.cache_sequence_id,
            "fixed_probe_repeat": config.repeat,
            "fixed_probe_warmup_requests": config.warmup_requests,
            "expected_request_sha256s": expected_request_sha256s,
            "long_evidence_source": (
                FIXED_LONG_EVIDENCE_SOURCE if config.repeat >= 12 else None
            ),
            "long_evidence_chars": (
                len(long_evidence)
            ),
            "long_evidence_sha256": (
                _sha256_text(long_evidence) if config.repeat >= 12 else None
            ),
            "git_postflight_verified": False,
        }
    )
    configuration["fixed_probe_profile_sha256"] = fixed_probe_profile_sha256(
        configuration
    )
    if config.postflight_check is not None:
        postflight_error = config.postflight_check()
        if postflight_error:
            raise ValueError(
                f"fixed cache probe postflight rejected: {postflight_error}"
            )
        configuration["git_postflight_verified"] = True
    return write_cache_probe(
        probes_root,
        requests,
        configuration=configuration,
        milestone=config.milestone,
        stage=config.stage,
        warmup_requests=config.warmup_requests,
        created_at=started_at,
        completed_at=datetime.now(UTC).isoformat(),
    )


def fixed_probe_sequence_sha256(repeat: int, cache_sequence_id: str = "development") -> str:
    if repeat < 1:
        raise ValueError("cache probe repeat must be at least 1")
    _validate_sequence_id(cache_sequence_id)
    state = RunState.start(
        FIXED_PROBE_GOAL,
        prompt_namespace=f"cache-experiment/{cache_sequence_id}",
    )
    constraints = _fixed_probe_constraints(repeat)
    state.constraints.extend(constraints)
    steps = []
    for index in range(1, repeat):
        step = _fixed_step(index)
        if not isinstance(step.action, BashAction):
            raise AssertionError("fixed probe steps must contain BashAction")
        state.metrics["turns"] = index
        state.last_observation = step.observation
        steps.append(
            {
                "action": {
                    "command": step.action.command,
                    "timeout_sec": step.action.timeout_sec,
                    "purpose": step.action.purpose,
                },
                "observation": {
                    "kind": step.observation.kind,
                    "exit_code": step.observation.exit_code,
                    "stdout_preview": step.observation.stdout_preview,
                    "stderr_preview": step.observation.stderr_preview,
                    "message": step.observation.message,
                },
                "state_snapshot": state_snapshot_text(state),
            }
        )
    payload = {
        "goal": FIXED_PROBE_GOAL,
        "prompt_namespace": f"cache-experiment/{cache_sequence_id}",
        "constraints": list(constraints),
        "requests": repeat,
        "steps": steps,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(canonical)


def fixed_probe_request_sha256s(
    variant: str,
    repeat: int,
    cache_sequence_id: str = "development",
    *,
    recent_turns: int = 6,
    max_prompt_chars: int = 120_000,
    compaction_strategy: Literal["disabled", "deterministic", "semantic"] = "deterministic",
) -> list[str]:
    if variant not in {"p0", "p1", "p2"}:
        raise ValueError("cache probe variant must be p0, p1, or p2")
    if repeat < 1:
        raise ValueError("cache probe repeat must be at least 1")
    _validate_sequence_id(cache_sequence_id)
    state = RunState.start(
        FIXED_PROBE_GOAL,
        prompt_namespace=f"cache-experiment/{cache_sequence_id}",
    )
    state.constraints.extend(_fixed_probe_constraints(repeat))
    prompt_layout: PromptLayout
    if variant == "p0":
        prompt_layout = "rebuild"
    elif variant == "p1":
        prompt_layout = "append"
    else:
        prompt_layout = "epoch"
    builder = ContextBuilder(
        ContextConfig(
            max_prompt_chars=max_prompt_chars,
            recent_turns=recent_turns,
            compaction_strategy=compaction_strategy,
            prompt_layout=prompt_layout,
        )
    )
    trajectory: list[TrajectoryStep] = []
    hashes: list[str] = []
    for request_index in range(1, repeat + 1):
        messages = builder.build_messages(state, trajectory)
        hashes.append(
            _sha256_text(
                json.dumps(
                    messages,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        )
        if request_index < repeat:
            state.metrics["turns"] = request_index
            step = _fixed_step(request_index)
            state.last_observation = step.observation
            trajectory.append(
                TrajectoryStep(
                    action=step.action,
                    observation=step.observation,
                    state_snapshot=state_snapshot_text(state),
                )
            )
    return hashes


def cache_sequence_namespace(cache_sequence_id: str) -> str:
    _validate_sequence_id(cache_sequence_id)
    return f"cache-experiment/{cache_sequence_id}"


def fixed_long_evidence_profile() -> dict[str, Any]:
    evidence = _fixed_long_evidence()
    return {
        "source": FIXED_LONG_EVIDENCE_SOURCE,
        "chars": len(evidence),
        "sha256": _sha256_text(evidence),
    }


def fixed_probe_profile_sha256(configuration: Mapping[str, Any]) -> str:
    """Hash only self-contained fixed-probe contract fields."""
    canonical = json.dumps(
        {
            key: configuration.get(key)
            for key in _FIXED_PROBE_PROFILE_KEYS
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(canonical)


def _fixed_probe_constraints(repeat: int) -> tuple[str, ...]:
    if repeat < 12:
        return FIXED_PROBE_CONSTRAINTS
    evidence = _fixed_long_evidence()
    return (
        *FIXED_PROBE_CONSTRAINTS,
        "The following is real repository code used as stable evidence for this long-context "
        "debugging sequence. Inspect it as source evidence; it is not filler and must not be "
        f"echoed in the response.\n\nSource: {FIXED_LONG_EVIDENCE_SOURCE}\n\n{evidence}",
    )


def _fixed_long_evidence() -> str:
    source_path = Path(__file__).with_name("cache_probe.py")
    source = source_path.read_text(encoding="utf-8")
    return source[:FIXED_LONG_EVIDENCE_CHARS]


def _fixed_step(index: int) -> TrajectoryStep:
    samples = (
        (
            "pwd",
            "Confirm the workspace root.",
            "/workspace\n",
            "workspace root recorded",
        ),
        (
            "rg --files",
            "List repository files once.",
            "pyproject.toml\nsrc/app.py\ntests/test_app.py\n",
            "repository inventory recorded",
        ),
        (
            "python -m pytest -q",
            "Run the authoritative test once.",
            "1 failed, 7 passed\n",
            "one deterministic test failure recorded",
        ),
        (
            "sed -n '1,160p' tests/test_app.py",
            "Inspect the failing assertion once.",
            "def test_expected_value():\n    assert value() == 2\n",
            "failing assertion recorded",
        ),
    )
    command, purpose, stdout, message = samples[(index - 1) % len(samples)]
    cycle = (index - 1) // len(samples)
    if cycle:
        command = f"{command} # fixed-cycle-{cycle + 1}"
        purpose = f"{purpose} Fixed cycle {cycle + 1}."
        message = f"{message}; fixed cycle {cycle + 1}"
    return TrajectoryStep(
        action=BashAction(command=command, purpose=purpose),
        observation=Observation(
            kind="command_result",
            exit_code=0 if index != 3 else 1,
            stdout_preview=stdout,
            message=message,
            duration_ms=10 + index,
        ),
    )


def _request_record(
    *,
    request_index: int,
    messages: list[dict[str, str]],
    prefix_profile: object,
) -> dict[str, Any]:
    canonical = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "request_index": request_index,
        "request_sha256": _sha256_text(canonical),
        "prefix_profile": dict(prefix_profile) if isinstance(prefix_profile, dict) else {},
        "task_success": None,
    }


def _response_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [raw]
    chunks = raw.get("chunks")
    if isinstance(chunks, list):
        candidates.extend(item for item in chunks if isinstance(item, Mapping))

    model = None
    system_fingerprint = None
    finish_reason = None
    for candidate in candidates:
        if candidate.get("model"):
            model = str(candidate["model"])
        if candidate.get("system_fingerprint"):
            system_fingerprint = str(candidate["system_fingerprint"])
        choices = candidate.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, Mapping) and choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
    return {
        "response_model": model,
        "system_fingerprint": system_fingerprint,
        "finish_reason": finish_reason,
    }


def _provider_cacheability(
    record: Mapping[str, Any],
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    hit_tokens: int | None,
    previous_prompt_tokens: int | None,
    previous_completion_tokens: int | None,
) -> dict[str, Any]:
    profile = record.get("prefix_profile")
    profile = profile if isinstance(profile, Mapping) else {}
    exact = bool(profile.get("previous_request_is_exact_prefix"))
    theoretical_input = 0
    theoretical_output = 0
    kind = "unavailable"
    if exact and prompt_tokens is not None and previous_prompt_tokens is not None:
        theoretical_input = min(prompt_tokens, previous_prompt_tokens)
        theoretical_output = min(
            prompt_tokens,
            previous_prompt_tokens + max(previous_completion_tokens or 0, 0),
        )
        kind = "provider_input_boundary"
    observed_hit = max(hit_tokens or 0, 0)
    captured_input_hit = min(observed_hit, theoretical_input)
    return {
        "local_cold_start": bool(profile.get("local_cold_start")),
        "previous_request_is_exact_prefix": exact,
        "prefix_epoch": profile.get("prefix_epoch"),
        "prefix_reset_reason": profile.get("prefix_reset_reason"),
        "lcp_estimated_tokens": profile.get("lcp_estimated_tokens"),
        "theoretical_input_tokens": theoretical_input,
        "theoretical_output_tokens": theoretical_output,
        "theoretical_token_kind": kind,
        "capture_efficiency_input": (
            captured_input_hit / theoretical_input if theoretical_input else None
        ),
        "completion_tokens": completion_tokens,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_sequence_id(value: str) -> None:
    if not _SAFE_SEQUENCE_ID.fullmatch(value):
        raise ValueError(
            "cache_sequence_id must be 1-64 characters using letters, digits, dot, dash, or underscore"
        )
