from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from minicc.core.provider import CompletionOptions, ModelProvider, ModelResponse
from minicc.core.state import RunState
from minicc.prompts.compaction import COMPACTION_SYSTEM_PROMPT, compaction_prompt
from minicc.trace.recorder import TraceRecorder, model_usage_to_dict


class CompactionError(RuntimeError):
    """The semantic compaction request did not produce a usable summary."""


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    input_chars: int
    output_chars: int


class ContextCompactor(Protocol):
    def compact(
        self,
        state: RunState,
        *,
        trajectory_text: str,
        existing_summary: str = "",
        retention_markers: tuple[str, ...] = (),
        source_steps: int = 0,
    ) -> CompactionResult:
        ...


class SemanticCompactor:
    """Model-backed, structured compaction used by the V2.1 A1 variant."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        trace: TraceRecorder | None = None,
        max_input_chars: int = 60_000,
        max_summary_chars: int = 12_000,
    ) -> None:
        self.provider = provider
        self.trace = trace
        self.max_input_chars = max_input_chars
        self.max_summary_chars = max_summary_chars

    def compact(
        self,
        state: RunState,
        *,
        trajectory_text: str,
        existing_summary: str = "",
        retention_markers: tuple[str, ...] = (),
        source_steps: int = 0,
    ) -> CompactionResult:
        input_text = _trim_text(trajectory_text, self.max_input_chars)
        if self.trace is not None:
            self.trace.semantic_compaction_started(
                state,
                source_steps=source_steps,
                input_chars=len(input_text),
            )
        state.metrics["semantic_compaction_requests"] = (
            state.metrics.get("semantic_compaction_requests", 0) + 1
        )
        try:
            response = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": COMPACTION_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": compaction_prompt(
                            input_text,
                            existing_summary=existing_summary,
                            retention_markers=retention_markers,
                            max_summary_chars=self.max_summary_chars,
                        ),
                    },
                ],
                options=CompletionOptions(
                    temperature=0.0,
                    stream=False,
                    include_usage=True,
                    json_mode=True,
                ),
            )
            summary = _trim_text(_parse_summary(response.text), self.max_summary_chars)
        except Exception as exc:
            if self.trace is not None:
                self.trace.semantic_compaction_failed(state, error=f"{type(exc).__name__}: {exc}")
            raise CompactionError(f"Semantic compaction failed: {exc}") from exc

        _record_usage(state, response)
        if self.trace is not None:
            self.trace.semantic_compaction_finished(
                state,
                source_steps=source_steps,
                input_chars=len(input_text),
                summary_chars=len(summary),
                usage=model_usage_to_dict(response.usage),
            )
        return CompactionResult(
            summary=summary,
            input_chars=len(input_text),
            output_chars=len(summary),
        )


def _parse_summary(text: str) -> str:
    payload_text = text.strip()
    if not payload_text.startswith("{"):
        match = re.search(r"\{.*\}", payload_text, re.DOTALL)
        if match:
            payload_text = match.group()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise CompactionError("response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise CompactionError("response must be a JSON object")
    summary = payload.get("summary") or payload.get("summary_markdown")
    if not isinstance(summary, str) or not summary.strip():
        raise CompactionError("response is missing a non-empty summary")
    return summary.strip()


def _record_usage(state: RunState, response: ModelResponse) -> None:
    usage = response.usage
    metric_map = {
        "semantic_compaction_prompt_tokens": usage.prompt_tokens,
        "semantic_compaction_completion_tokens": usage.completion_tokens,
        "semantic_compaction_total_tokens": usage.total_tokens,
        "semantic_compaction_cached_tokens": usage.cached_tokens,
    }
    for key, value in metric_map.items():
        if value is not None:
            state.metrics[key] = state.metrics.get(key, 0) + value
    state.metrics["semantic_compaction_latency_ms"] = (
        state.metrics.get("semantic_compaction_latency_ms", 0) + response.latency_ms
    )


def _trim_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n...[truncated]...\n"
    if max_chars <= len(marker):
        return text[:max_chars]
    keep = max(max_chars - len(marker), 0)
    head = keep // 2
    tail = keep - head
    tail_text = text[-tail:] if tail else ""
    return text[:head] + marker + tail_text
