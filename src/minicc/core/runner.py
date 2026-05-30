from __future__ import annotations

from dataclasses import dataclass

from minicc.core.prompt import PromptBuilder
from minicc.core.protocol import Action, ProtocolError, parse_action
from minicc.core.provider import CompletionOptions, ModelProvider, ModelUsage
from minicc.core.state import Observation, RunState, TrajectoryStep


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
        prompt_builder: PromptBuilder | None = None,
        config: ModelTurnConfig | None = None,
    ) -> None:
        self.provider = provider
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.config = config or ModelTurnConfig()
        self.protocol_errors = 0

    def next_turn(self, state: RunState, trajectory: list[TrajectoryStep]) -> ModelTurn:
        messages = self.prompt_builder.build(state, trajectory)
        response = self.provider.complete(messages, options=self.config.model_options)
        state.metrics["turns"] += 1
        _accumulate_usage(state, response.usage, response.latency_ms)

        try:
            action = parse_action(
                response.text,
                max_timeout_sec=self.config.max_action_timeout_sec,
            )
            self.protocol_errors = 0
            return ModelTurn(action=action)
        except ProtocolError as exc:
            self.protocol_errors += 1
            state.metrics["protocol_errors"] += 1
            observation = Observation(
                kind="protocol_error",
                message=exc.message,
                stderr_preview=exc.raw_text[:4000],
            )
            if self.protocol_errors > self.config.max_protocol_errors:
                state.status = "failed"
                state.state_summary = "Run failed because the model repeatedly violated the action protocol."
                return ModelTurn(action=None, observation=observation, should_continue=False)
            return ModelTurn(action=None, observation=observation)


def _accumulate_usage(state: RunState, usage: ModelUsage, latency_ms: int) -> None:
    metric_map = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cached_tokens": usage.cached_tokens,
        "prompt_cache_hit_tokens": usage.cache_hit_tokens,
        "prompt_cache_miss_tokens": usage.cache_miss_tokens,
    }
    for key, value in metric_map.items():
        if value is not None:
            state.metrics[key] += value
    state.metrics["latency_ms"] += latency_ms
