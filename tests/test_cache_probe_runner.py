import hashlib
import json
from pathlib import Path

import pytest

from minicc.core.context import ContextConfig
from minicc.core.provider import CompletionOptions, ModelResponse, ModelUsage, NativeToolCall
from minicc.evals.cache_probe_runner import (
    CacheProbeRunConfig,
    fixed_probe_profile_sha256,
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
            text="",
            raw={
                "model": "fixed-model",
                "system_fingerprint": "fixed-backend",
                "choices": [{"finish_reason": "stop"}],
            },
            usage=ModelUsage(
                prompt_tokens=1000 + index,
                completion_tokens=5,
                total_tokens=1005 + index,
                cache_hit_tokens=10 * index,
                cache_miss_tokens=1000 + index - (10 * index),
            ),
            latency_ms=20 + index,
            tool_calls=(
                NativeToolCall(id=f"call-{index}", name="bash", arguments='{"command":"true"}'),
            ),
        )


class InvalidProtocolProvider(RecordingProvider):
    def complete(self, messages, *, options=None):
        response = super().complete(messages, options=options)
        return ModelResponse(
            text=response.text,
            raw=response.raw,
            usage=response.usage,
            latency_ms=response.latency_ms,
            tool_calls=(
                NativeToolCall(id="call-invalid", name="bash", arguments='{"command":""}'),
            ),
        )


class NonBashProvider(RecordingProvider):
    def complete(self, messages, *, options=None):
        response = super().complete(messages, options=options)
        return ModelResponse(
            text=response.text,
            raw=response.raw,
            usage=response.usage,
            latency_ms=response.latency_ms,
            tool_calls=(
                NativeToolCall(id="call-final", name="final", arguments='{"answer":"done"}'),
            ),
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


def test_fixed_probe_p2_preserves_all_twelve_requests_with_small_recent_window(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()
    bundle = run_fixed_cache_probe(
        provider,
        probes_root=tmp_path / "probes",
        config=CacheProbeRunConfig(
            variant="p2",
            repeat=12,
            cache_sequence_id="round-epoch",
            context=ContextConfig(prompt_layout="epoch", recent_turns=1),
            model_options=CompletionOptions(),
            configuration={},
        ),
    )

    assert len(provider.messages) == 12
    for previous, current in zip(provider.messages, provider.messages[1:], strict=False):
        assert current[: len(previous)] == previous
    report = json.loads(bundle.report_json_path.read_text(encoding="utf-8"))
    assert report["requests"][-1]["prefix_epoch"] == 1
    assert report["requests"][-1]["theoretical_input_tokens"] == 1011
    assert report["configuration"]["long_evidence_source"] == (
        "src/minicc/evals/cache_probe.py"
    )
    assert report["configuration"]["long_evidence_chars"] == 8_000
    assert len(report["configuration"]["long_evidence_sha256"]) == 64
    assert report["configuration"]["expected_request_sha256s"] == [
        request["request_sha256"]
        for request in report["requests"]
    ]
    assert report["configuration"]["fixed_probe_profile_sha256"] == (
        fixed_probe_profile_sha256(report["configuration"])
    )
    assert fixed_probe_sequence_sha256(5, "round-1") != fixed_probe_sequence_sha256(
        5,
        "round-2",
    )


def test_v212_p1_long_probe_keeps_windowed_baseline_behavior(tmp_path: Path) -> None:
    provider = RecordingProvider()
    run_fixed_cache_probe(
        provider,
        probes_root=tmp_path / "probes",
        config=CacheProbeRunConfig(
            variant="p1",
            repeat=12,
            cache_sequence_id="round-windowed",
            context=ContextConfig(prompt_layout="append", recent_turns=6),
            model_options=CompletionOptions(),
            configuration={},
            milestone="v2.1.2-development",
        ),
    )

    assert provider.messages[6][: len(provider.messages[5])] == provider.messages[5]
    assert provider.messages[7][: len(provider.messages[6])] != provider.messages[6]


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


def test_fixed_probe_postflight_failure_writes_no_bundle(tmp_path: Path) -> None:
    probes_root = tmp_path / "probes"

    with pytest.raises(ValueError, match="postflight rejected"):
        run_fixed_cache_probe(
            RecordingProvider(),
            probes_root=probes_root,
            config=CacheProbeRunConfig(
                variant="p1",
                repeat=2,
                cache_sequence_id="postflight-failure",
                context=ContextConfig(prompt_layout="append"),
                model_options=CompletionOptions(),
                configuration={},
                postflight_check=lambda: "Git state changed",
            ),
        )

    assert not probes_root.exists()
