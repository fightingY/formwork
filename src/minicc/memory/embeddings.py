"""OpenAI-compatible ``/embeddings`` adapter (spec §9).

The memory store takes a plain ``Embedder`` callable; this module is the real
HTTP implementation behind ``memory.embedding_enabled``.  Like every other
memory subsystem it fails soft at the caller boundary: the adapter itself
raises a classified :class:`EmbeddingError`, and the store degrades that row or
query to BM25 — a vector outage must never fail recall.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers only
    from minicc.config import Settings


class EmbeddingError(RuntimeError):
    """One failed embeddings call, with a stable failure code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OpenAIEmbeddings:
    """``POST {base_url}/embeddings`` with ``{"model", "input"}``.

    Follows the same route shape as the completion providers (``base_url``,
    ``api_key``, ``headers``, ``timeout_ms``) so one ``providers:`` entry can
    back both.  Failures raise :class:`EmbeddingError` with ``auth`` /
    ``http`` / ``timeout`` / ``shape`` codes.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        headers: dict[str, str] | None = None,
        timeout_ms: int = 30_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}", **(headers or {})}
        self.timeout_sec = max(1.0, timeout_ms / 1000)
        # Client reuse keeps connection pooling; transport is injection for tests.
        self._client = httpx.Client(
            headers=self.headers, timeout=self.timeout_sec, transport=transport
        )

    def __call__(self, text: str) -> list[float]:
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": text}
        try:
            response = self._client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise EmbeddingError("timeout", f"embeddings request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("http", f"embeddings request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise EmbeddingError("auth", f"embeddings auth failed ({response.status_code})")
        if response.status_code >= 400:
            raise EmbeddingError("http", f"embeddings request failed ({response.status_code})")
        try:
            embedding = response.json()["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise EmbeddingError(
                "shape", "embeddings response missing data[0].embedding"
            ) from exc
        if not isinstance(embedding, list) or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in embedding
        ):
            raise EmbeddingError("shape", "embeddings response is not a numeric array")
        return [float(value) for value in embedding]


def embedder_from_settings(settings: Settings) -> OpenAIEmbeddings | None:
    """Build the embedder from ``memory.embedding_route`` (or the default route).

    Returns ``None`` — never raises — when embeddings are not configured, so the
    caller simply runs pure BM25 (spec §9: 无向量时退化为 BM25，绝不失败).
    """
    route_name = settings.memory.embedding_route or settings.default_provider
    route: Any = settings.providers.get(route_name)
    if route is None or not getattr(route, "base_url", ""):
        return None
    return OpenAIEmbeddings(
        base_url=str(route.base_url),
        api_key=str(getattr(route, "api_key", "") or ""),
        model=str(settings.memory.embedding_model or getattr(route, "model", "")),
        headers=dict(getattr(route, "headers", None) or {}),
        timeout_ms=int(getattr(route, "timeout_ms", 30_000) or 30_000),
    )


__all__ = ["EmbeddingError", "OpenAIEmbeddings", "embedder_from_settings"]
