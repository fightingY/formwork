from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4


CACHE_PROBE_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class CacheProbeBundle:
    probe_id: str
    probe_dir: Path
    manifest_path: Path
    requests_path: Path
    report_json_path: Path
    report_markdown_path: Path


def new_cache_probe_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"cache-probe-{timestamp}-{uuid4().hex[:8]}"


def build_cache_probe_report(
    requests: Sequence[Mapping[str, Any]],
    *,
    configuration: Mapping[str, Any],
    probe_id: str | None = None,
    milestone: str = "v2.1.1",
    stage: str = "development_precheck",
    warmup_requests: int = 2,
    created_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    if not requests:
        raise ValueError("cache probe requires at least one request record")
    if warmup_requests < 0:
        raise ValueError("warmup_requests must be non-negative")

    probe_id = _safe_identifier(probe_id or new_cache_probe_id())
    normalized = [
        normalize_cache_request(record, request_index=index)
        for index, record in enumerate(requests, start=1)
    ]
    cache = summarize_cache_requests(normalized)
    steady_state_requests = normalized[min(warmup_requests, len(normalized)) :]
    steady_state_cache = summarize_cache_requests(steady_state_requests)
    stable_prefix = _stable_prefix_summary(normalized, configuration)
    passed = all(
        record["request_success"] and record["task_success"] is True
        for record in normalized
    )
    variant = str(
        configuration.get("prompt_cache_variant")
        or configuration.get("cache_variant")
        or configuration.get("variant")
        or ""
    )
    created_at_value = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": CACHE_PROBE_SCHEMA_VERSION,
        "entity_type": "prompt_cache_probe_report",
        "probe_id": probe_id,
        "milestone": milestone,
        "stage": stage,
        "created_at": created_at_value,
        "completed_at": completed_at or created_at_value,
        "status": "completed",
        "result": "PASS" if passed else "FAIL",
        "passed": passed,
        "variant": variant,
        "configuration": dict(configuration),
        "request_count": len(normalized),
        "warmup_requests": min(warmup_requests, len(normalized)),
        "steady_state_request_count": len(steady_state_requests),
        "cache": cache,
        "steady_state_cache": steady_state_cache,
        "stable_prefix": stable_prefix,
        "requests": normalized,
    }


def normalize_cache_request(
    record: Mapping[str, Any],
    *,
    request_index: int,
) -> dict[str, Any]:
    usage_value = record.get("usage")
    usage = usage_value if isinstance(usage_value, Mapping) else record
    profile_value = record.get("prefix_profile", record.get("stable_prefix_profile"))
    profile = profile_value if isinstance(profile_value, Mapping) else {}
    prompt_tokens = _optional_int(usage.get("prompt_tokens"))
    hit_tokens = _first_int(
        usage,
        "cache_hit_tokens",
        "prompt_cache_hit_tokens",
        "cache_observed_hit_tokens",
    )
    miss_tokens = _first_int(
        usage,
        "cache_miss_tokens",
        "prompt_cache_miss_tokens",
    )
    cached_tokens = _first_int(usage, "cached_tokens")
    miss_tokens_derived = False

    if hit_tokens is None and cached_tokens is not None and prompt_tokens is not None:
        hit_tokens = cached_tokens
    if hit_tokens is not None and miss_tokens is None and prompt_tokens is not None:
        miss_tokens = max(prompt_tokens - hit_tokens, 0)
        miss_tokens_derived = True

    metrics_reported = hit_tokens is not None and miss_tokens is not None
    if not metrics_reported:
        cache_state = "unsupported"
    elif hit_tokens == 0:
        cache_state = "zero_hit"
    else:
        cache_state = "nonzero_hit"

    task_success = _optional_bool(record.get("task_success"))
    request_success = _optional_bool(
        record.get("request_success", record.get("success", True))
    )
    return {
        "request_index": _optional_int(
            record.get("request_index", record.get("index", request_index))
        )
        or request_index,
        "request_success": True if request_success is None else request_success,
        "task_success": task_success,
        "prompt_tokens": prompt_tokens,
        "cache_hit_tokens": hit_tokens,
        "cache_miss_tokens": miss_tokens,
        "miss_tokens_derived": miss_tokens_derived,
        "cache_metrics_reported": metrics_reported,
        "cache_state": cache_state,
        "latency_ms": _optional_int(record.get("latency_ms")),
        "attempt_count": _optional_int(record.get("attempt_count")),
        "request_sha256": _optional_text(
            record.get("request_sha256", record.get("messages_sha256"))
        ),
        "response_sha256": _optional_text(record.get("response_sha256")),
        "stable_prefix_sha256": _optional_text(
            record.get(
                "stable_prefix_sha256",
                record.get("stable_prefix_hash", profile.get("sha256")),
            )
        ),
        "stable_prefix_chars": _optional_int(
            record.get(
                "stable_prefix_chars",
                profile.get("content_chars", profile.get("chars")),
            )
        ),
        "stable_prefix_estimated_tokens": _optional_int(
            record.get(
                "stable_prefix_estimated_tokens",
                profile.get("estimated_tokens"),
            )
        ),
        "response_model": _optional_text(record.get("response_model", record.get("model"))),
        "system_fingerprint": _optional_text(record.get("system_fingerprint")),
        "finish_reason": _optional_text(record.get("finish_reason")),
        "error": _optional_text(record.get("error")),
    }


def summarize_cache_requests(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records = list(requests)
    reported = [record for record in records if bool(record.get("cache_metrics_reported"))]
    hit_tokens = sum(_integer(record.get("cache_hit_tokens")) for record in reported)
    miss_tokens = sum(_integer(record.get("cache_miss_tokens")) for record in reported)
    observed_prompt_tokens = hit_tokens + miss_tokens
    request_count = len(records)
    metric_requests = len(reported)
    unreported_requests = request_count - metric_requests
    if metric_requests == 0:
        coverage_status = "unsupported"
        cache_state = "unsupported"
        weighted_hit_rate = None
    else:
        coverage_status = "complete" if unreported_requests == 0 else "partial"
        cache_state = "zero_hit" if hit_tokens == 0 else "nonzero_hit"
        weighted_hit_rate = hit_tokens / observed_prompt_tokens if observed_prompt_tokens else 0.0

    latency_values = [
        value
        for record in records
        if (value := _optional_int(record.get("latency_ms"))) is not None
    ]
    task_values = [
        value
        for record in records
        if (value := _optional_bool(record.get("task_success"))) is not None
    ]
    successful_requests = sum(
        _optional_bool(record.get("request_success")) is not False for record in records
    )
    return {
        "request_count": request_count,
        "successful_requests": successful_requests,
        "request_success_rate": successful_requests / request_count if request_count else None,
        "metric_requests": metric_requests,
        "unreported_requests": unreported_requests,
        "coverage_status": coverage_status,
        "cache_state": cache_state,
        "hit_tokens": hit_tokens,
        "miss_tokens": miss_tokens,
        "observed_prompt_tokens": observed_prompt_tokens,
        "weighted_hit_rate": weighted_hit_rate,
        "prompt_tokens": sum(_integer(record.get("prompt_tokens")) for record in records),
        "latency_samples": len(latency_values),
        "latency_ms_total": sum(latency_values),
        "latency_ms_mean": sum(latency_values) / len(latency_values) if latency_values else None,
        "latency_ms_min": min(latency_values) if latency_values else None,
        "latency_ms_max": max(latency_values) if latency_values else None,
        "task_results_reported": len(task_values),
        "task_successes": sum(task_values),
        "task_success_rate": sum(task_values) / len(task_values) if task_values else None,
        "miss_tokens_derived": any(bool(record.get("miss_tokens_derived")) for record in reported),
    }


def write_immutable_cache_probe(
    probes_root: Path,
    report: Mapping[str, Any],
) -> CacheProbeBundle:
    probe_id = _safe_identifier(str(report.get("probe_id") or ""))
    if report.get("entity_type") != "prompt_cache_probe_report":
        raise ValueError("not a prompt cache probe report")
    requests = report.get("requests")
    if not isinstance(requests, list) or not requests:
        raise ValueError("cache probe report has no request records")

    probes_root = probes_root.resolve()
    probes_root.mkdir(parents=True, exist_ok=True)
    probe_dir = probes_root / probe_id
    if probe_dir.exists():
        raise FileExistsError(f"Cache probe evidence is immutable and already exists: {probe_dir}")
    temporary = probes_root / f".{probe_id}.tmp-{uuid4().hex[:8]}"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        requests_path = temporary / "requests.jsonl"
        requests_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests),
            encoding="utf-8",
        )
        report_path = temporary / "report.json"
        report_path.write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path = temporary / "report.md"
        markdown_path.write_text(format_cache_probe_markdown(report), encoding="utf-8")
        manifest = {
            "schema_version": CACHE_PROBE_SCHEMA_VERSION,
            "entity_type": "prompt_cache_probe",
            "probe_id": probe_id,
            "milestone": report.get("milestone", ""),
            "stage": report.get("stage", ""),
            "created_at": report.get("created_at"),
            "completed_at": report.get("completed_at"),
            "status": report.get("status"),
            "result": report.get("result"),
            "variant": report.get("variant"),
            "configuration": dict(report.get("configuration") or {}),
            "request_count": report.get("request_count"),
            "artifacts": {
                "requests": _artifact_entry(requests_path),
                "report_json": _artifact_entry(report_path),
                "report_markdown": _artifact_entry(markdown_path),
            },
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(probe_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    return CacheProbeBundle(
        probe_id=probe_id,
        probe_dir=probe_dir,
        manifest_path=probe_dir / "manifest.json",
        requests_path=probe_dir / "requests.jsonl",
        report_json_path=probe_dir / "report.json",
        report_markdown_path=probe_dir / "report.md",
    )


def write_cache_probe(
    probes_root: Path,
    requests: Sequence[Mapping[str, Any]],
    *,
    configuration: Mapping[str, Any],
    probe_id: str | None = None,
    milestone: str = "v2.1.1",
    stage: str = "development_precheck",
    warmup_requests: int = 2,
    created_at: str | None = None,
    completed_at: str | None = None,
) -> CacheProbeBundle:
    report = build_cache_probe_report(
        requests,
        configuration=configuration,
        probe_id=probe_id,
        milestone=milestone,
        stage=stage,
        warmup_requests=warmup_requests,
        created_at=created_at,
        completed_at=completed_at,
    )
    return write_immutable_cache_probe(probes_root, report)


def load_cache_probe_report(
    path: Path,
    *,
    verify_manifest: bool = False,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("entity_type") != "prompt_cache_probe_report":
        raise ValueError(f"not a prompt cache probe report: {path}")
    if not isinstance(payload.get("requests"), list):
        raise ValueError(f"cache probe report has no requests: {path}")
    if verify_manifest:
        _verify_cache_probe_manifest(path, payload)
        payload["_evidence_integrity_verified"] = True
    return payload


def format_cache_probe_markdown(report: Mapping[str, Any]) -> str:
    cache = report["cache"]
    steady = report["steady_state_cache"]
    stable = report["stable_prefix"]
    lines = [
        "# miniCC V2.1.1 Prompt Cache Fixed-Sequence Probe",
        "",
        f"Result: **{report['result']}**",
        f"Probe: `{report['probe_id']}`",
        f"Variant: `{report.get('variant') or 'unknown'}`",
        f"Requests: {report['request_count']} "
        f"(warm-up={report['warmup_requests']}, steady-state={report['steady_state_request_count']})",
        "",
        "## Aggregate",
        "",
        "| Scope | Requests | Reported | Unreported | Hit tokens | Miss tokens | "
        "Weighted hit rate | Prompt tokens | Latency total/mean ms | Task result |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        _cache_markdown_row("all", cache),
        _cache_markdown_row("steady-state", steady),
        "",
        "## Stable prefix",
        "",
        f"- Hash: `{stable.get('sha256') or 'unavailable'}`",
        f"- Consistent across requests: {'yes' if stable['consistent'] else 'no'}",
        f"- Characters min/max: {stable['chars_min']}/{stable['chars_max']}",
        "- Estimated reusable tokens min/max: "
        f"{stable['estimated_tokens_min']}/{stable['estimated_tokens_max']}",
        "",
        "## Requests",
        "",
        "| # | Result | Attempts | Cache state | Prompt | Hit | Miss | Hit rate | Latency ms | Task |",
        "|---:|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for request in report["requests"]:
        hit = request.get("cache_hit_tokens")
        miss = request.get("cache_miss_tokens")
        rate = (
            hit / (hit + miss)
            if isinstance(hit, int) and isinstance(miss, int) and hit + miss
            else (0.0 if isinstance(hit, int) and isinstance(miss, int) else None)
        )
        task = request.get("task_success")
        lines.append(
            f"| {request['request_index']} | "
            f"{'PASS' if request['request_success'] else 'FAIL'} | "
            f"{_display(request.get('attempt_count'))} | "
            f"{request['cache_state']} | {_display(request.get('prompt_tokens'))} | "
            f"{_display(hit)} | {_display(miss)} | {_format_rate(rate)} | "
            f"{_display(request.get('latency_ms'))} | "
            f"{'n/a' if task is None else ('PASS' if task else 'FAIL')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _stable_prefix_summary(
    requests: Sequence[Mapping[str, Any]],
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    hashes = {
        str(value)
        for request in requests
        if (value := request.get("stable_prefix_sha256"))
    }
    configured_hash = _optional_text(
        configuration.get("stable_prefix_sha256", configuration.get("stable_prefix_hash"))
    )
    if configured_hash:
        hashes.add(configured_hash)
    chars = [
        value
        for request in requests
        if (value := _optional_int(request.get("stable_prefix_chars"))) is not None
    ]
    configured_chars = _optional_int(configuration.get("stable_prefix_chars"))
    if configured_chars is not None:
        chars.append(configured_chars)
    tokens = [
        value
        for request in requests
        if (value := _optional_int(request.get("stable_prefix_estimated_tokens"))) is not None
    ]
    configured_tokens = _optional_int(configuration.get("stable_prefix_estimated_tokens"))
    if configured_tokens is not None:
        tokens.append(configured_tokens)
    return {
        "sha256": next(iter(hashes)) if len(hashes) == 1 else None,
        "hashes": sorted(hashes),
        "consistent": bool(hashes) and len(hashes) == 1,
        "chars_min": min(chars) if chars else None,
        "chars_max": max(chars) if chars else None,
        "estimated_tokens_min": min(tokens) if tokens else None,
        "estimated_tokens_max": max(tokens) if tokens else None,
    }


def _cache_markdown_row(label: str, cache: Mapping[str, Any]) -> str:
    task_rate = cache.get("task_success_rate")
    return (
        f"| {label} | {cache['request_count']} | {cache['metric_requests']} | "
        f"{cache['unreported_requests']} | {cache['hit_tokens']} | {cache['miss_tokens']} | "
        f"{_format_rate(cache.get('weighted_hit_rate'))} | {cache['prompt_tokens']} | "
        f"{cache['latency_ms_total']}/{_display_number(cache.get('latency_ms_mean'))} | "
        f"{_format_rate(task_rate)} |"
    )


def _artifact_entry(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _verify_cache_probe_manifest(path: Path, report: Mapping[str, Any]) -> None:
    manifest_path = path.parent / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid cache probe manifest: {manifest_path}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("entity_type") != "prompt_cache_probe"
        or manifest.get("probe_id") != report.get("probe_id")
        or path.parent.name != report.get("probe_id")
        or manifest.get("stage") != report.get("stage")
        or manifest.get("milestone") != report.get("milestone")
        or manifest.get("created_at") != report.get("created_at")
        or manifest.get("completed_at") != report.get("completed_at")
        or manifest.get("result") != report.get("result")
        or manifest.get("variant") != report.get("variant")
        or manifest.get("configuration") != report.get("configuration")
        or _integer(manifest.get("request_count")) != _integer(report.get("request_count"))
    ):
        raise ValueError(f"cache probe manifest does not match report: {path}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"cache probe manifest has no artifact hashes: {manifest_path}")
    expected_artifacts = {
        "requests": "requests.jsonl",
        "report_json": "report.json",
        "report_markdown": "report.md",
    }
    if set(artifacts) != set(expected_artifacts):
        raise ValueError(f"cache probe manifest has incomplete artifact hashes: {manifest_path}")
    for name, expected_relative in expected_artifacts.items():
        entry = artifacts[name]
        if not isinstance(entry, Mapping):
            raise ValueError(f"invalid cache probe artifact entry: {manifest_path}")
        relative = str(entry.get("path") or "")
        if relative != expected_relative:
            raise ValueError(f"unsafe cache probe artifact path: {relative!r}")
        artifact_path = path.parent / relative
        if not artifact_path.is_file():
            raise ValueError(f"cache probe artifact is missing: {artifact_path}")
        data = artifact_path.read_bytes()
        if (
            _integer(entry.get("bytes")) != len(data)
            or str(entry.get("sha256") or "") != hashlib.sha256(data).hexdigest()
        ):
            raise ValueError(f"cache probe artifact hash mismatch: {artifact_path}")
    try:
        request_records = [
            json.loads(line)
            for line in (path.parent / "requests.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid cache probe request evidence: {path.parent / 'requests.jsonl'}") from exc
    if request_records != report.get("requests"):
        raise ValueError(f"cache probe requests do not match report: {path}")
    _verify_cache_probe_semantics(report)


def _verify_cache_probe_semantics(report: Mapping[str, Any]) -> None:
    requests = report.get("requests", [])
    warmup = _integer(report.get("warmup_requests"))
    if (
        _integer(report.get("request_count")) != len(requests)
        or warmup < 0
        or warmup > len(requests)
        or _integer(report.get("steady_state_request_count")) != len(requests) - warmup
    ):
        raise ValueError("cache probe request counts are inconsistent")
    for position, request in enumerate(requests, start=1):
        if not isinstance(request, Mapping):
            raise ValueError("cache probe request record is invalid")
        hit = _optional_int(request.get("cache_hit_tokens"))
        miss = _optional_int(request.get("cache_miss_tokens"))
        metrics_reported = hit is not None and miss is not None
        expected_state = (
            "unsupported"
            if not metrics_reported
            else "zero_hit"
            if hit == 0
            else "nonzero_hit"
        )
        prompt = _optional_int(request.get("prompt_tokens"))
        attempt_count = _optional_int(request.get("attempt_count"))
        if (
            _integer(request.get("request_index")) != position
            or request.get("cache_metrics_reported") is not metrics_reported
            or request.get("cache_state") != expected_state
            or (attempt_count is not None and attempt_count < 1)
            or (
                metrics_reported
                and prompt is not None
                and prompt != _integer(hit) + _integer(miss)
            )
        ):
            raise ValueError(f"cache probe request semantics are inconsistent: request {position}")
    expected_passed = all(
        request.get("request_success") is True
        and request.get("task_success") is True
        for request in requests
    )
    configuration = report.get("configuration") or {}
    expected_variant = str(
        configuration.get("prompt_cache_variant")
        or configuration.get("cache_variant")
        or configuration.get("variant")
        or ""
    )
    expected = {
        "status": "completed",
        "result": "PASS" if expected_passed else "FAIL",
        "passed": expected_passed,
        "variant": expected_variant,
        "cache": summarize_cache_requests(requests),
        "steady_state_cache": summarize_cache_requests(requests[warmup:]),
        "stable_prefix": _stable_prefix_summary(requests, configuration),
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("cache probe derived fields do not match request evidence")


def _safe_identifier(value: str) -> str:
    if not value or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid cache probe id: {value!r}")
    return value


def _first_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _optional_int(mapping.get(key))
        if value is not None:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int:
    return _optional_int(value) or 0


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _format_rate(value: float | None) -> str:
    return "unsupported" if value is None else f"{value:.2%}"


def _display(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _display_number(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}"
