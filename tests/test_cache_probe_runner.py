import hashlib
import json
from pathlib import Path

from minicc.core.context import ContextConfig
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage
from minicc.evals.cache_probe_runner import (
    CacheProbeRunConfig,
    fixed_probe_request_sha256s,
    fixed_probe_sequence_sha256,
    run_fixed_cache_probe,
)


class RecordingProvider:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages, *, options=None):
        self.messages.append(messages)
        index = len(self.messages)
        return ModelResponse(
            text='{"type":"bash","command":"true"}',
            raw={
                "model": "fixed-model",
                "system_fingerprint": "fixed-backend",
                "choices": [{"finish_reason": "stop"}],
            },
            usage=ModelUsage(
                prompt_tokens=100 + index,
                completion_tokens=5,
                total_tokens=105 + index,
                cache_hit_tokens=10 * index,
                cache_miss_tokens=100 + index - (10 * index),
            ),
            latency_ms=20 + index,
        )


class InvalidProtocolProvider(RecordingProvider):
    def complete(self, messages, *, options=None):
        response = super().complete(messages, options=options)
        return ModelResponse(
            text="not-json",
            raw=response.raw,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )


class NonBashProvider(RecordingProvider):
    def complete(self, messages, *, options=None):
        response = super().complete(messages, options=options)
        return ModelResponse(
            text='{"type":"final","answer":"done"}',
            raw=response.raw,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )


def test_fixed_probe_p1_builds_nested_requests_and_writes_immutable_bundle(tmp_path: Path) -> None:
    provider = RecordingProvider()
    bundle = run_fixed_cache_probe(
        provider,
        probes_root=tmp_path / "probes",
        config=CacheProbeRunConfig(
            variant="p1",
            repeat=5,
            cache_sequence_id="round-1",
            context=ContextConfig(prompt_layout="append"),
            model_options=CompletionOptions(),
            configuration={
                "base_url": "https://provider.test/v1",
                "model": "fixed-model",
                "temperature": 0.0,
                "json_mode": True,
                "max_completion_tokens": 128,
                "git_commit": "abc123",
                "compaction_strategy": "deterministic",
            },
            stage="formal_acceptance",
        ),
    )

    assert len(provider.messages) == 5
    actual_hashes = [
        hashlib.sha256(
            json.dumps(
                messages,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for messages in provider.messages
    ]
    assert actual_hashes == fixed_probe_request_sha256s("p1", 5, "round-1")
    for previous, current in zip(provider.messages, provider.messages[1:], strict=False):
        assert current[: len(previous)] == previous
    assert all(
        "Budget status:" not in message["content"]
        for messages in provider.messages
        for message in messages
    )
    report = bundle.report_json_path.read_text(encoding="utf-8")
    assert '"variant": "p1"' in report
    assert '"request_count": 5' in report
    assert '"cache_state": "nonzero_hit"' in report


def test_fixed_probe_sequence_digest_is_stable_within_round_and_namespaced_between_rounds() -> None:
    assert fixed_probe_sequence_sha256(5, "round-1") == fixed_probe_sequence_sha256(
        5,
        "round-1",
    )
    assert fixed_probe_sequence_sha256(5, "round-1") != fixed_probe_sequence_sha256(
        5,
        "round-2",
    )


def test_fixed_probe_rejects_variant_layout_mismatch(tmp_path: Path) -> None:
    try:
        run_fixed_cache_probe(
            RecordingProvider(),
            probes_root=tmp_path,
            config=CacheProbeRunConfig(
                variant="p1",
                repeat=5,
                cache_sequence_id="round-1",
                context=ContextConfig(prompt_layout="rebuild"),
                model_options=CompletionOptions(),
                configuration={},
            ),
        )
    except ValueError as exc:
        assert "prompt_layout=append" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_fixed_probe_fails_when_provider_output_violates_action_protocol(tmp_path: Path) -> None:
    bundle = run_fixed_cache_probe(
        InvalidProtocolProvider(),
        probes_root=tmp_path,
        config=CacheProbeRunConfig(
            variant="p0",
            repeat=1,
            cache_sequence_id="invalid-output",
            context=ContextConfig(prompt_layout="rebuild"),
            model_options=CompletionOptions(),
            configuration={},
        ),
    )

    report = bundle.report_json_path.read_text(encoding="utf-8")
    assert '"result": "FAIL"' in report
    assert '"task_success": false' in report


def test_fixed_probe_requires_a_bash_action_not_just_valid_protocol(tmp_path: Path) -> None:
    bundle = run_fixed_cache_probe(
        NonBashProvider(),
        probes_root=tmp_path,
        config=CacheProbeRunConfig(
            variant="p0",
            repeat=1,
            cache_sequence_id="non-bash-output",
            context=ContextConfig(prompt_layout="rebuild"),
            model_options=CompletionOptions(),
            configuration={},
        ),
    )

    report = bundle.report_json_path.read_text(encoding="utf-8")
    assert '"result": "FAIL"' in report
    assert '"task_success": false' in report
    assert "ProbeActionError: expected bash, got final" in report
