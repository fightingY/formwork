from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import gcd
from typing import Any

from minicc.core.protocol import Action, ProtocolError, parse_action
from minicc.core.provider import CompletionOptions, ModelProvider, ModelUsage
from minicc.core.state import Observation, RunState
from minicc.trace.recorder import TraceRecorder


@dataclass(frozen=True)
class ModelTurnConfig:
    max_protocol_errors: int = 2
    max_action_timeout_sec: int = 120
    model_options: CompletionOptions = CompletionOptions()


@dataclass
class ModelTurn:
    action: Action | None
    observation: Observation | None = None
    should_continue: bool = True


class ModelTurnRunner:
    def __init__(
        self,
        provider: ModelProvider,
        *,
        config: ModelTurnConfig | None = None,
        trace: TraceRecorder | None = None,
    ) -> None:
        self.provider = provider
        self.provider_name = str(
            getattr(provider, "provider_name", type(provider).__name__)
        )
        self.config = config or ModelTurnConfig()
        self.protocol_errors = 0
        self.trace = trace

    def next_turn(
        self,
        state: RunState,
        messages: list[dict[str, str]],
    ) -> ModelTurn:
        state.metrics["provider_name"] = self.provider_name
        response = self.provider.complete(messages, options=self.config.model_options)
        state.metrics["turns"] += 1
        _accumulate_response_identity(state, response.raw)
        _accumulate_usage(
            state,
            response.usage,
            response.latency_ms,
            attempt_count=response.attempt_count,
        )
        if self.trace is not None:
            self.trace.model_response(
                state,
                response.text,
                response.latency_ms,
                response.usage,
                attempt_count=response.attempt_count,
                retry_reasons=response.retry_reasons,
            )

        try:
            action = parse_action(
                response.text,
                max_timeout_sec=self.config.max_action_timeout_sec,
            )
            self.protocol_errors = 0
            if self.trace is not None:
                self.trace.action_parsed(state, action)
            return ModelTurn(action=action)
        except ProtocolError as exc:
            self.protocol_errors += 1
            state.metrics["protocol_errors"] += 1
            observation = Observation(
                kind="protocol_error",
                message=exc.message,
                stderr_preview=exc.raw_text[:4000],
            )
            if self.trace is not None:
                self.trace.action_parsed(state, None)
                self.trace.observation_created(state, observation)
            if self.protocol_errors > self.config.max_protocol_errors:
                state.status = "failed"
                state.state_summary = "Run failed because the model repeatedly violated the action protocol."
                return ModelTurn(
                    action=None,
                    observation=observation,
                    should_continue=False,
                )
            return ModelTurn(action=None, observation=observation)


def _accumulate_usage(
    state: RunState,
    usage: ModelUsage,
    latency_ms: int,
    *,
    attempt_count: int = 1,
) -> None:
    metric_map = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_tokens": usage.cached_tokens,
        "prompt_cache_hit_tokens": usage.cache_hit_tokens,
        "prompt_cache_miss_tokens": usage.cache_miss_tokens,
    }
    for key, value in metric_map.items():
        if value is not None:
            state.metrics[key] = state.metrics.get(key, 0) + value

    observed_hit_tokens: int | None = None
    observed_prompt_tokens: int | None = None
    if usage.cache_hit_tokens is not None and usage.cache_miss_tokens is not None:
        observed_hit_tokens = usage.cache_hit_tokens
        observed_prompt_tokens = usage.cache_hit_tokens + usage.cache_miss_tokens
    elif usage.cached_tokens is not None and usage.prompt_tokens is not None:
        observed_hit_tokens = usage.cached_tokens
        observed_prompt_tokens = usage.prompt_tokens

    if observed_hit_tokens is None or observed_prompt_tokens is None:
        state.metrics["cache_unreported_requests"] = state.metrics.get("cache_unreported_requests", 0) + 1
    else:
        state.metrics["cache_metrics_available"] = True
        state.metrics["cache_metric_requests"] = state.metrics.get("cache_metric_requests", 0) + 1
        state.metrics["cache_observed_hit_tokens"] = (
            state.metrics.get("cache_observed_hit_tokens", 0) + observed_hit_tokens
        )
        state.metrics["cache_observed_prompt_tokens"] = (
            state.metrics.get("cache_observed_prompt_tokens", 0) + observed_prompt_tokens
        )
        total_observed = state.metrics["cache_observed_prompt_tokens"]
        state.metrics["cache_hit_rate"] = (
            state.metrics["cache_observed_hit_tokens"] / total_observed if total_observed else 0.0
        )
        _accumulate_cacheability(
            state,
            usage,
            observed_hit_tokens=observed_hit_tokens,
            observed_prompt_tokens=observed_prompt_tokens,
        )
    state.metrics["latency_ms"] = state.metrics.get("latency_ms", 0) + latency_ms
    normalized_attempts = max(int(attempt_count or 1), 1)
    state.metrics["provider_request_attempts"] = (
        int(state.metrics.get("provider_request_attempts", 0)) + normalized_attempts
    )
    if normalized_attempts > 1:
        state.metrics["provider_retried_requests"] = (
            int(state.metrics.get("provider_retried_requests", 0)) + 1
        )
    _record_cache_request(state, usage, latency_ms=latency_ms)


def _record_cache_request(
    state: RunState,
    usage: ModelUsage,
    *,
    latency_ms: int,
) -> None:
    prompt_tokens = usage.prompt_tokens
    cache_read_tokens = usage.cache_hit_tokens
    if cache_read_tokens is None:
        cache_read_tokens = usage.cached_tokens
    uncached_tokens = usage.cache_miss_tokens
    if uncached_tokens is None and prompt_tokens is not None and cache_read_tokens is not None:
        uncached_tokens = max(prompt_tokens - cache_read_tokens, 0)
    models = state.metrics.get("provider_response_models", [])
    model = models[-1] if isinstance(models, list) and models else None
    compaction_count = int(state.metrics.get("context_compactions", 0) or 0)
    request = {
        "request_index": int(state.metrics.get("cache_prefix_request_index", 0) or 0),
        "provider": state.metrics.get("provider_name"),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": None,
        "uncached_tokens": uncached_tokens,
        "cache_hit_rate": (
            usage.cache_hit_rate
            if usage.cache_hit_rate is not None
            else (
                cache_read_tokens / prompt_tokens
                if cache_read_tokens is not None and prompt_tokens
                else None
            )
        ),
        "system_prefix_estimated_tokens": state.metrics.get(
            "cache_layer_system_estimated_tokens_current"
        ),
        "project_prefix_estimated_tokens": state.metrics.get(
            "cache_layer_project_estimated_tokens_current"
        ),
        "conversation_estimated_tokens": state.metrics.get(
            "cache_layer_conversation_estimated_tokens_current"
        ),
        "prefix_hash": state.metrics.get("cache_prefix_request_sha256"),
        "previous_request_is_prefix": bool(
            state.metrics.get("cache_prefix_previous_is_exact")
        ),
        "longest_common_prefix_estimated_tokens": state.metrics.get(
            "cache_prefix_lcp_estimated_tokens"
        ),
        "prefix_reset_reason": state.metrics.get("cache_prefix_reset_reason"),
        "compaction_id": compaction_count or None,
        "latency_ms": latency_ms,
    }
    raw_requests = state.metrics.get("cache_requests", [])
    requests = list(raw_requests) if isinstance(raw_requests, list) else []
    requests.append(request)
    state.metrics["cache_requests"] = requests


def _accumulate_cacheability(
    state: RunState,
    usage: ModelUsage,
    *,
    observed_hit_tokens: int,
    observed_prompt_tokens: int,
) -> None:
    cold_start = bool(state.metrics.get("cache_prefix_local_cold_start"))
    previous_is_exact = bool(state.metrics.get("cache_prefix_previous_is_exact"))
    previous_prompt = _optional_int(state.metrics.get("cache_previous_provider_prompt_tokens"))
    previous_completion = _optional_int(state.metrics.get("cache_previous_provider_completion_tokens"))
    theoretical_kind = "unavailable"
    theoretical_input = 0
    theoretical_output = 0

    if previous_is_exact and previous_prompt is not None:
        theoretical_input = min(observed_prompt_tokens, previous_prompt)
        theoretical_output = min(
            observed_prompt_tokens,
            previous_prompt + max(previous_completion or 0, 0),
        )
        theoretical_kind = "provider_input_boundary"
    elif not cold_start:
        estimated_prompt = max(
            int(state.metrics.get("cache_prefix_current_estimated_tokens", 0) or 0),
            0,
        )
        estimated_lcp = max(
            int(state.metrics.get("cache_prefix_lcp_estimated_tokens", 0) or 0),
            0,
        )
        if estimated_prompt > 0 and estimated_lcp > 0:
            theoretical_input = min(
                observed_prompt_tokens,
                round(observed_prompt_tokens * estimated_lcp / estimated_prompt),
            )
            theoretical_output = theoretical_input
            theoretical_kind = "estimated_message_lcp"

    eligible_hit = (
        min(observed_hit_tokens, theoretical_input)
        if theoretical_input > 0
        else 0
    )
    state.metrics["cache_theoretical_input_tokens"] = (
        int(state.metrics.get("cache_theoretical_input_tokens", 0)) + theoretical_input
    )
    state.metrics["cache_theoretical_output_tokens"] = (
        int(state.metrics.get("cache_theoretical_output_tokens", 0)) + theoretical_output
    )
    state.metrics["cache_capture_observed_hit_tokens"] = (
        int(state.metrics.get("cache_capture_observed_hit_tokens", 0)) + eligible_hit
    )
    input_total = int(state.metrics["cache_theoretical_input_tokens"])
    output_total = int(state.metrics["cache_theoretical_output_tokens"])
    capture_hit_total = int(state.metrics["cache_capture_observed_hit_tokens"])
    state.metrics["cache_capture_efficiency_input"] = (
        capture_hit_total / input_total if input_total else None
    )
    state.metrics["cache_capture_efficiency_output"] = (
        capture_hit_total / output_total if output_total else None
    )
    steady_state_observed = bool(
        state.metrics.get("cache_steady_state_observed")
    )
    if observed_hit_tokens > 0 and not steady_state_observed:
        steady_state_observed = True
        state.metrics["cache_steady_state_observed"] = True
        state.metrics["cache_steady_state_start_request_index"] = int(
            state.metrics.get("cache_prefix_request_index", 0) or 0
        )
    if steady_state_observed:
        state.metrics["cache_steady_state_hit_tokens"] = (
            int(state.metrics.get("cache_steady_state_hit_tokens", 0)) + observed_hit_tokens
        )
        state.metrics["cache_steady_state_prompt_tokens"] = (
            int(state.metrics.get("cache_steady_state_prompt_tokens", 0)) + observed_prompt_tokens
        )
        state.metrics["cache_steady_state_request_count"] = (
            int(state.metrics.get("cache_steady_state_request_count", 0)) + 1
        )
        steady_prompt = int(state.metrics["cache_steady_state_prompt_tokens"])
        state.metrics["cache_steady_state_hit_rate"] = (
            int(state.metrics["cache_steady_state_hit_tokens"]) / steady_prompt
            if steady_prompt
            else None
        )
    if observed_hit_tokens > 0:
        previous_block = max(
            int(state.metrics.get("cache_empirical_hit_block_tokens", 0) or 0),
            0,
        )
        state.metrics["cache_empirical_hit_block_tokens"] = (
            observed_hit_tokens if previous_block == 0 else gcd(previous_block, observed_hit_tokens)
        )

    state.metrics["cache_theoretical_input_tokens_current"] = theoretical_input
    state.metrics["cache_theoretical_output_tokens_current"] = theoretical_output
    state.metrics["cache_theoretical_token_kind_current"] = theoretical_kind
    state.metrics["cache_capture_efficiency_input_current"] = (
        min(observed_hit_tokens, theoretical_input) / theoretical_input
        if theoretical_input
        else None
    )
    state.metrics["cache_previous_provider_prompt_tokens"] = (
        usage.prompt_tokens if usage.prompt_tokens is not None else observed_prompt_tokens
    )
    state.metrics["cache_previous_provider_completion_tokens"] = usage.completion_tokens or 0


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _accumulate_response_identity(state: RunState, raw: Mapping[str, Any]) -> None:
    models: set[str] = set()
    fingerprints: set[str] = set()
    candidates: list[Mapping[str, Any]] = [raw]
    chunks = raw.get("chunks")
    if isinstance(chunks, list):
        candidates.extend(item for item in chunks if isinstance(item, Mapping))
    for candidate in candidates:
        model = candidate.get("model")
        fingerprint = candidate.get("system_fingerprint")
        if model:
            models.add(str(model))
        if fingerprint:
            fingerprints.add(str(fingerprint))
    state.metrics["provider_response_models"] = sorted(
        {
            *state.metrics.get("provider_response_models", []),
            *models,
        }
    )
    state.metrics["provider_system_fingerprints"] = sorted(
        {
            *state.metrics.get("provider_system_fingerprints", []),
            *fingerprints,
        }
    )
