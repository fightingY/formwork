from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from minicc.core.provider import (
    TIMEOUT,
    TRANSPORT,
    LlmFailure,
    ProviderError,
    _classify_http_status,
)

# V4.1 M5：模型发现对 OpenAI 兼容端点做一次有界 `GET {base_url}/models`，把「目录」列给
# 使用者。发现失败复用 :class:`LlmFailure` 合同：401/403 归类 `AUTH`（复用 provider 的
# `_classify_http_status`），其余「拿不到模型列表」的情况（非 JSON / 无 ``data`` 数组 /
# 响应超大）统一用本模块专属的 ``DISCOVERY_FAILED`` 标识——它不是 completion 失败码，
# 因此不入 `ALL_CODES`，只有 discovery 消费方认识它。

DISCOVERY_FAILED = "DISCOVERY_FAILED"

DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB，防超大目录把内存打爆。


@dataclass(frozen=True)
class ModelInfo:
    """One model entry from ``GET /models`` (only the fields miniCC consumes)."""

    id: str
    context_window: int | None = None
    max_output_tokens: int | None = None


def discover_models(
    base_url: str,
    api_key: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[ModelInfo]:
    """List models available on an OpenAI-compatible endpoint.

    Bounded read: ``GET {base_url}/models``, bearer auth, capped payload
    (:paramref:`max_bytes`); 401/403 surface as ``AUTH`` (via shared HTTP
    classification), transport/timeout as ``TRANSPORT``/``TIMEOUT``, and any
    otherwise-parseable-but-not-a-catalog response as ``DISCOVERY_FAILED``.
    Malformed or non-catalog entries in ``data`` are skipped, not fatal.
    """
    url = f"{base_url.rstrip('/')}/models"
    request_headers = dict(headers or {})
    request_headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=timeout_ms / 1000.0) as client:
            with client.stream("GET", url, headers=request_headers) as response:
                response.raise_for_status()
                body = _read_bounded(response, max_bytes)
    except httpx.HTTPStatusError as exc:
        raise ProviderError(failure=_classify_http_status(exc.response)) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            failure=LlmFailure(
                message=f"Model discovery request failed: {type(exc).__name__}",
                code=TIMEOUT if isinstance(exc, httpx.TimeoutException) else TRANSPORT,
            )
        ) from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise ProviderError(
            failure=LlmFailure(
                message="Provider /models endpoint returned non-JSON.",
                code=DISCOVERY_FAILED,
            )
        ) from None
    if not isinstance(payload, Mapping):
        raise ProviderError(
            failure=LlmFailure(
                message="Provider /models endpoint returned a non-object JSON body.",
                code=DISCOVERY_FAILED,
            )
        )

    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderError(
            failure=LlmFailure(
                message="Provider /models endpoint response has no `data` array.",
                code=DISCOVERY_FAILED,
            )
        )

    models: list[ModelInfo] = []
    for item in data:
        model_id = _model_id(item)
        if model_id is None:
            continue
        models.append(
            ModelInfo(
                id=model_id,
                context_window=_optional_int(item, "context_window", "context_length"),
                max_output_tokens=_optional_int(item, "max_tokens", "max_output_tokens"),
            )
        )
    return models


def _read_bounded(response: httpx.Response, max_bytes: int) -> str:
    """Read the response body, failing cleanly if it exceeds ``max_bytes``.

    Checks ``content-length`` first as a fast path, then accumulates streamed
    chunks so a lying / missing length header still cannot grow memory unbounded.
    """
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise _oversized(max_bytes)
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise _oversized(max_bytes)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _oversized(max_bytes: int) -> ProviderError:
    return ProviderError(
        failure=LlmFailure(
            message=f"Provider /models response exceeds the {max_bytes}-byte limit.",
            code=DISCOVERY_FAILED,
        )
    )


def _model_id(item: Any) -> str | None:
    if not isinstance(item, Mapping):
        return None
    model_id = item.get("id")
    if model_id is None:
        return None
    text = str(model_id).strip()
    return text or None


def _optional_int(item: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = item.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None