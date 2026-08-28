from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import gcd
from typing import Any

from minicc.core.protocol import Action, ProtocolError, parse_tool_call
from minicc.core.provider import CompletionOptions, ModelProvider, ModelUsage
from minicc.core.state import Observation, RunState
from minicc.trace.recorder import TraceRecorder


@dataclass(frozen=True)
class ModelTurnConfig:
    max_action_timeout_sec: int = 120
    model_options: CompletionOptions = CompletionOptions()
    max_tool_calls_per_step: int = 16


@dataclass
class ModelTurn:
    actions: tuple[Action, ...] = ()
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
        self.trace = trace

    def next_turn(
        self,
        state: RunState,
        messages: list[dict[str, str]],
        *,
        provider: ModelProvider | None = None,
        attempt_count: int = 1,
        retry_reasons: tuple[str, ...] = (),
    ) -> ModelTurn:
        # ``provider`` 是 retry/failover 执行器在重试不同 route 时注入的适配器；
        # 缺省回落到构造时绑定的默认适配器。每次调用按实际适配器覆盖 provider 标识。
        active_provider = provider or self.provider
        state.metrics["provider_name"] = str(
            getattr(active_provider, "provider_name", type(active_provider).__name__)
        )
        response = active_provider.complete(messages, options=self.config.model_options)
        state.metrics["turns"] += 1
        _accumulate_response_identity(state, response.raw)
        _accumulate_usage(
            state,
            response.usage,
            response.latency_ms,
        )
        if self.trace is not None:
            self.trace.model_response(
                state,
                response.text,
                response.latency_ms,
                response.usage,
                attempt_count=attempt_count,
                retry_reasons=retry_reasons,
                tool_calls=response.tool_calls,
            )

        if not response.tool_calls:
            # tool_choice="required" 下 provider 仍未产出 tool_call 违反了 provider 契约
            # （不是模型可恢复的场景，理论上不该发生）——直接终止 run。旧协议的
            # "协议错误重试预算"是为文本 JSON 解析失败设计的，原生模式下没有对应场景。
            state.status = "failed"
            state.state_summary = (
                "Run failed because the provider returned no tool_calls despite tool_choice=required."
            )
            observation = Observation(kind="command_error", message=state.state_summary)
            if self.trace is not None:
                self.trace.observation_created(state, observation)
            return ModelTurn(actions=(), observation=observation, should_continue=False)

        actions: list[Action] = []
        for tool_call in response.tool_calls:
            try:
                arguments = json.loads(tool_call.arguments) if tool_call.arguments else {}
                if not isinstance(arguments, dict):
                    raise ValueError("tool_call.arguments must decode to a JSON object")
                action = parse_tool_call(
                    tool_call.id,
                    tool_call.name,
                    arguments,
                    max_timeout_sec=self.config.max_action_timeout_sec,
                )
            except (ValueError, ProtocolError) as exc:
                # 单个 tool_call 参数损坏是运行期可恢复错误（provider 吐出的
                # function.arguments 不是合法 JSON，或参数校验失败），不终止 run：
                # 构造合成 observation 反馈给模型，让它在下一轮纠正。
                observation = Observation(
                    kind="protocol_error",
                    message=f"tool_call {tool_call.id} ({tool_call.name}) rejected: {exc}",
                )
                if self.trace is not None:
                    self.trace.observation_created(state, observation)
                return ModelTurn(actions=(), observation=observation)
            actions.append(action)
            if self.trace is not None:
                self.trace.action_parsed(state, action)
        return ModelTurn(actions=tuple(actions))


def _accumulate_usage(
    state: RunState,
    usage: ModelUsage,
    latency_ms: int,
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
    # attempt 计数（provider_request_attempts / provider_retried_requests）不再在这里累计：
    # 传输重试上移到 core/retry.py 的成功返回处统计，此处只记录单次 attempt 的用量。
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
