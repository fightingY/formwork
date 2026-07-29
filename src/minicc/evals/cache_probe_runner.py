from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from minicc.core.context import STABLE_PREFIX, ContextBuilder, ContextConfig, state_snapshot_text
from minicc.core.protocol import BashAction, ProtocolError, parse_action
from minicc.core.provider import CompletionOptions, ModelProvider, ProviderError
from minicc.core.state import Observation, RunState, TrajectoryStep
from minicc.evals.cache_probe import CacheProbeBundle, write_cache_probe


FIXED_PROBE_GOAL = (
    "Inspect the supplied repository evidence and choose the next minimal verification action. "
    "This is a deterministic transport-level cache probe; rely only on the supplied observations."
)
FIXED_PROBE_CONSTRAINTS = (
    "Return exactly one Bash-first JSON action.",
    "Do not assume that a displayed command was actually executed outside this fixed sequence.",
)
_SAFE_SEQUENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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


def run_fixed_cache_probe(
    provider: ModelProvider,
    *,
    probes_root: Path,
    config: CacheProbeRunConfig,
) -> CacheProbeBundle:
    started_at = datetime.now(timezone.utc).isoformat()
    if config.variant not in {"p0", "p1"}:
        raise ValueError("cache probe variant must be p0 or p1")
    if config.repeat < 1:
        raise ValueError("cache probe repeat must be at least 1")
    _validate_sequence_id(config.cache_sequence_id)
    if config.context.recent_turns < config.repeat - 1:
        raise ValueError(
            "cache probe recent_turns must retain every fixed-sequence step"
        )
    expected_layout = "append" if config.variant == "p1" else "rebuild"
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
    state.constraints.extend(FIXED_PROBE_CONSTRAINTS)
    builder = ContextBuilder(config.context)
    trajectory: list[TrajectoryStep] = []
    requests: list[dict[str, Any]] = []

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
                    "response_sha256": _sha256_text(response.text),
                    **_response_metadata(response.raw),
                }
            )
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

    configuration = dict(config.configuration)
    configuration.update(
        {
            "cache_variant": config.variant,
            "prompt_layout": config.context.prompt_layout,
            "system_prefix_sha256": _sha256_text(STABLE_PREFIX),
            "dynamic_sequence_sha256": fixed_probe_sequence_sha256(
                config.repeat,
                config.cache_sequence_id,
            ),
            "cache_sequence_id": config.cache_sequence_id,
        }
    )
    return write_cache_probe(
        probes_root,
        requests,
        configuration=configuration,
        milestone=config.milestone,
        stage=config.stage,
        warmup_requests=config.warmup_requests,
        created_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


def fixed_probe_sequence_sha256(repeat: int, cache_sequence_id: str = "development") -> str:
    if repeat < 1:
        raise ValueError("cache probe repeat must be at least 1")
    _validate_sequence_id(cache_sequence_id)
    state = RunState.start(
        FIXED_PROBE_GOAL,
        prompt_namespace=f"cache-experiment/{cache_sequence_id}",
    )
    state.constraints.extend(FIXED_PROBE_CONSTRAINTS)
    steps = []
    for index in range(1, repeat):
        step = _fixed_step(index)
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
        "constraints": list(FIXED_PROBE_CONSTRAINTS),
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
    compaction_strategy: str = "deterministic",
) -> list[str]:
    if variant not in {"p0", "p1"}:
        raise ValueError("cache probe variant must be p0 or p1")
    if repeat < 1:
        raise ValueError("cache probe repeat must be at least 1")
    _validate_sequence_id(cache_sequence_id)
    state = RunState.start(
        FIXED_PROBE_GOAL,
        prompt_namespace=f"cache-experiment/{cache_sequence_id}",
    )
    state.constraints.extend(FIXED_PROBE_CONSTRAINTS)
    builder = ContextBuilder(
        ContextConfig(
            max_prompt_chars=max_prompt_chars,
            recent_turns=recent_turns,
            compaction_strategy=compaction_strategy,
            prompt_layout="append" if variant == "p1" else "rebuild",
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_sequence_id(value: str) -> None:
    if not _SAFE_SEQUENCE_ID.fullmatch(value):
        raise ValueError(
            "cache_sequence_id must be 1-64 characters using letters, digits, dot, dash, or underscore"
        )
