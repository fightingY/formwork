import json

import pytest

from minicc.evals.cache_probe import (
    build_cache_probe_report,
    format_cache_probe_markdown,
    load_cache_probe_report,
    write_immutable_cache_probe,
)


def test_cache_probe_reports_actual_request_and_steady_state_metrics() -> None:
    records = [
        _request(1, hit=0, miss=100, latency=10),
        _request(2, hit=0, miss=100, latency=20),
        _request(3, hit=40, miss=60, latency=30),
        _request(4, hit=50, miss=50, latency=40),
        _request(5, hit=60, miss=40, latency=50),
    ]

    report = build_cache_probe_report(
        records,
        configuration={"prompt_cache_variant": "p1", "model": "fixed"},
        probe_id="cache-probe-p1-r1",
        stage="formal_acceptance",
    )
    markdown = format_cache_probe_markdown(report)

    assert report["request_count"] == 5
    assert report["cache"]["hit_tokens"] == 150
    assert report["cache"]["miss_tokens"] == 350
    assert report["cache"]["weighted_hit_rate"] == 0.3
    assert report["cache"]["latency_ms_mean"] == 30
    assert report["cache"]["steady_state_start_request_index"] == 3
    assert report["cache"]["steady_state_request_count"] == 3
    assert report["cache"]["steady_state_weighted_hit_rate"] == 0.5
    assert report["steady_state_request_count"] == 3
    assert report["steady_state_cache"]["hit_tokens"] == 150
    assert report["steady_state_cache"]["miss_tokens"] == 150
    assert report["steady_state_cache"]["weighted_hit_rate"] == 0.5
    assert report["stable_prefix"]["consistent"] is True
    assert report["stable_prefix"]["estimated_tokens_min"] == 200
    assert "| 3 | PASS | n/a | nonzero_hit | 100 | 40 | 60 | 40.00% | 30 | PASS |" in markdown


def test_cache_probe_distinguishes_unsupported_zero_and_nonzero_hits() -> None:
    unsupported = build_cache_probe_report(
        [{"usage": {"prompt_tokens": 100}, "latency_ms": 1}],
        configuration={"prompt_cache_variant": "p0"},
        probe_id="cache-probe-unsupported",
        warmup_requests=0,
    )
    zero = build_cache_probe_report(
        [{"usage": {"prompt_tokens": 100, "cache_hit_tokens": 0, "cache_miss_tokens": 100}}],
        configuration={"prompt_cache_variant": "p0"},
        probe_id="cache-probe-zero",
        warmup_requests=0,
    )
    nonzero = build_cache_probe_report(
        [{"usage": {"prompt_tokens": 100, "cache_hit_tokens": 25, "cache_miss_tokens": 75}}],
        configuration={"prompt_cache_variant": "p1"},
        probe_id="cache-probe-hit",
        warmup_requests=0,
    )

    assert unsupported["cache"]["coverage_status"] == "unsupported"
    assert unsupported["cache"]["cache_state"] == "unsupported"
    assert unsupported["cache"]["weighted_hit_rate"] is None
    assert zero["cache"]["coverage_status"] == "complete"
    assert zero["cache"]["cache_state"] == "zero_hit"
    assert zero["cache"]["weighted_hit_rate"] == 0.0
    assert nonzero["cache"]["cache_state"] == "nonzero_hit"
    assert nonzero["cache"]["weighted_hit_rate"] == 0.25


def test_cache_probe_derives_miss_tokens_for_cached_tokens_style_usage() -> None:
    report = build_cache_probe_report(
        [
            {
                "usage": {"prompt_tokens": 100, "cached_tokens": 30},
                "prefix_profile": {
                    "sha256": "profile-hash",
                    "content_chars": 640,
                    "estimated_tokens": 160,
                },
            }
        ],
        configuration={"prompt_cache_variant": "p1"},
        probe_id="cache-probe-derived",
        warmup_requests=0,
    )

    assert report["cache"]["hit_tokens"] == 30
    assert report["cache"]["miss_tokens"] == 70
    assert report["cache"]["miss_tokens_derived"] is True
    assert report["requests"][0]["miss_tokens_derived"] is True
    assert report["stable_prefix"]["sha256"] == "profile-hash"
    assert report["stable_prefix"]["estimated_tokens_min"] == 160


def test_cache_probe_requires_prefix_hash_and_successful_probe_outcomes() -> None:
    report = build_cache_probe_report(
        [
            {
                "request_success": True,
                "task_success": False,
                "usage": {
                    "prompt_tokens": 100,
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 100,
                },
                "stable_prefix_estimated_tokens": 20,
            }
        ],
        configuration={"prompt_cache_variant": "p0"},
        probe_id="cache-probe-invalid-output",
        warmup_requests=0,
    )

    assert report["passed"] is False
    assert report["stable_prefix"]["consistent"] is False


def test_cache_probe_writer_is_atomic_and_immutable(tmp_path) -> None:
    report = build_cache_probe_report(
        [_request(index, hit=index, miss=100 - index, latency=10) for index in range(1, 6)],
        configuration={"prompt_cache_variant": "p1", "git_commit": "abc123"},
        probe_id="cache-probe-p1-r1",
        stage="formal_acceptance",
    )

    bundle = write_immutable_cache_probe(tmp_path / "cache-probes", report)
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))

    assert bundle.requests_path.read_text(encoding="utf-8").count("\n") == 5
    assert manifest["entity_type"] == "prompt_cache_probe"
    assert manifest["request_count"] == 5
    assert len(manifest["artifacts"]["report_json"]["sha256"]) == 64
    assert load_cache_probe_report(
        bundle.report_json_path,
        verify_manifest=True,
    )["probe_id"] == report["probe_id"]
    assert not list((tmp_path / "cache-probes").glob(".*.tmp-*"))
    with pytest.raises(FileExistsError, match="immutable"):
        write_immutable_cache_probe(tmp_path / "cache-probes", report)
    manifest["artifacts"] = {}
    bundle.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete artifact hashes"):
        load_cache_probe_report(bundle.report_json_path, verify_manifest=True)


def test_cache_probe_strict_loader_rejects_forged_derived_result(tmp_path) -> None:
    report = build_cache_probe_report(
        [
            {
                **_request(1, hit=0, miss=100, latency=10),
                "task_success": False,
            }
        ],
        configuration={"prompt_cache_variant": "p0"},
        probe_id="cache-probe-forged-result",
        stage="formal_acceptance",
        warmup_requests=0,
    )
    report["passed"] = True
    report["result"] = "PASS"
    bundle = write_immutable_cache_probe(tmp_path / "cache-probes", report)

    with pytest.raises(ValueError, match="derived fields"):
        load_cache_probe_report(bundle.report_json_path, verify_manifest=True)


def test_cache_probe_strict_loader_keeps_schema_v1_evidence_readable(tmp_path) -> None:
    report = build_cache_probe_report(
        [_request(1, hit=10, miss=90, latency=10)],
        configuration={"prompt_cache_variant": "p1"},
        probe_id="cache-probe-legacy-v1",
        warmup_requests=0,
    )
    report["schema_version"] = 1
    legacy_keys = {
        "request_count",
        "successful_requests",
        "request_success_rate",
        "metric_requests",
        "unreported_requests",
        "coverage_status",
        "cache_state",
        "hit_tokens",
        "miss_tokens",
        "observed_prompt_tokens",
        "weighted_hit_rate",
        "prompt_tokens",
        "latency_samples",
        "latency_ms_total",
        "latency_ms_mean",
        "latency_ms_min",
        "latency_ms_max",
        "task_results_reported",
        "task_successes",
        "task_success_rate",
        "miss_tokens_derived",
    }
    report["cache"] = {
        key: value for key, value in report["cache"].items() if key in legacy_keys
    }
    report["steady_state_cache"] = {
        key: value
        for key, value in report["steady_state_cache"].items()
        if key in legacy_keys
    }
    for request in report["requests"]:
        for key in (
            "completion_tokens",
            "prefix_epoch",
            "local_cold_start",
            "previous_request_is_exact_prefix",
            "prefix_reset_reason",
            "lcp_estimated_tokens",
            "theoretical_input_tokens",
            "theoretical_output_tokens",
            "theoretical_token_kind",
            "capture_efficiency_input",
        ):
            request.pop(key, None)

    bundle = write_immutable_cache_probe(tmp_path / "cache-probes", report)

    loaded = load_cache_probe_report(bundle.report_json_path, verify_manifest=True)
    assert loaded["schema_version"] == 1


def _request(index: int, *, hit: int, miss: int, latency: int) -> dict:
    return {
        "request_index": index,
        "request_success": True,
        "task_success": True,
        "usage": {
            "prompt_tokens": hit + miss,
            "cache_hit_tokens": hit,
            "cache_miss_tokens": miss,
        },
        "latency_ms": latency,
        "request_sha256": f"request-{index}",
        "response_sha256": f"response-{index}",
        "stable_prefix_sha256": "stable-prefix",
        "stable_prefix_chars": 800,
        "stable_prefix_estimated_tokens": 200,
    }
