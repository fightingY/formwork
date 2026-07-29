from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
        self.config = config or ModelTurnConfig()
        self.protocol_errors = 0
        self.trace = trace

    def next_turn(
        self,
        state: RunState,
        messages: list[dict[str, str]],
    ) -> ModelTurn:
        response = self.provider.complete(messages, options=self.config.model_options)
        state.metrics["turns"] += 1
        _accumulate_usage(
            state,
            response.usage,
            response.latency_ms,
            attempt_count=response.attempt_count,
        )
        _accumulate_response_identity(state, response.raw)
        if self.trace is not None:
            self.trace.model_response(
                state,
                response.text,
                response.latency_ms,
                response.usage,
                attempt_count=response.attempt_count,
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
    state.metrics["latency_ms"] = state.metrics.get("latency_ms", 0) + latency_ms
    normalized_attempts = max(int(attempt_count or 1), 1)
    state.metrics["provider_request_attempts"] = (
        int(state.metrics.get("provider_request_attempts", 0)) + normalized_attempts
    )
    if normalized_attempts > 1:
        state.metrics["provider_retried_requests"] = (
            int(state.metrics.get("provider_retried_requests", 0)) + 1
        )


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
