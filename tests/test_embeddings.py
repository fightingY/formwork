"""Deterministic tests for the /embeddings adapter (spec §9)."""

import json

import httpx

from minicc.config import (
    BudgetSettings,
    ContextSettings,
    MemorySettings,
    PolicySettings,
    ProviderRoute,
    SandboxSettings,
    Settings,
)
from minicc.memory.embeddings import EmbeddingError, OpenAIEmbeddings, embedder_from_settings


def _adapter(handler) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        base_url="https://relay.example.com/v1",
        api_key="sk-test",
        model="text-embedding-3-small",
        transport=httpx.MockTransport(handler),
    )


def test_openai_embeddings_posts_and_parses() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.25, -0.5, 1.0]}, {"extra": True}]},
        )

    vector = _adapter(handler)("deploy the auth service")

    assert seen["url"] == "https://relay.example.com/v1/embeddings"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"] == {"model": "text-embedding-3-small", "input": "deploy the auth service"}
    assert vector == [0.25, -0.5, 1.0]


def test_embeddings_auth_failure_is_classified() -> None:
    adapter = _adapter(lambda request: httpx.Response(401, json={"error": "bad key"}))
    try:
        adapter("hello")
    except EmbeddingError as exc:
        assert exc.code == "auth"
    else:  # pragma: no cover
        raise AssertionError("expected EmbeddingError")


def test_embeddings_http_failure_is_classified() -> None:
    adapter = _adapter(lambda request: httpx.Response(500, text="boom"))
    try:
        adapter("hello")
    except EmbeddingError as exc:
        assert exc.code == "http"
    else:  # pragma: no cover
        raise AssertionError("expected EmbeddingError")


def test_embeddings_shape_failure_is_classified() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={"data": []}))
    try:
        adapter("hello")
    except EmbeddingError as exc:
        assert exc.code == "shape"
    else:  # pragma: no cover
        raise AssertionError("expected EmbeddingError")


def test_embeddings_transport_error_is_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("relay down")

    adapter = _adapter(handler)
    try:
        adapter("hello")
    except EmbeddingError as exc:
        assert exc.code == "http"
    else:  # pragma: no cover
        raise AssertionError("expected EmbeddingError")


# --- settings wiring ---------------------------------------------------------


def _settings(**memory: object) -> Settings:
    return Settings(
        sandbox=SandboxSettings(),
        budget=BudgetSettings(),
        context=ContextSettings(),
        policy=PolicySettings(),
        providers={
            "main": ProviderRoute(
                name="main",
                base_url="https://chat.example.com/v1",
                api_key="sk-chat",
                model="gpt-test",
                timeout_ms=120_000,
            ),
            "embed": ProviderRoute(
                name="embed",
                base_url="https://embed.example.com/v1/",
                api_key="sk-embed",
                model="text-embedding-3-small",
                timeout_ms=5_000,
            ),
        },
        default_provider="main",
        memory=MemorySettings(**memory),  # type: ignore[arg-type]
    )


def test_embedder_from_settings_prefers_dedicated_route() -> None:
    embedder = embedder_from_settings(
        _settings(embedding_enabled=True, embedding_route="embed")
    )
    assert embedder is not None
    assert embedder.base_url == "https://embed.example.com/v1"
    assert embedder.model == "text-embedding-3-small"
    assert embedder.timeout_sec == 5.0


def test_embedder_from_settings_falls_back_to_default_route() -> None:
    embedder = embedder_from_settings(
        _settings(embedding_enabled=True, embedding_model="embed-chat")
    )
    assert embedder is not None
    assert embedder.base_url == "https://chat.example.com/v1"
    assert embedder.model == "embed-chat"  # explicit embedding_model overrides


def test_embedder_from_settings_missing_route_returns_none() -> None:
    assert (
        embedder_from_settings(_settings(embedding_enabled=True, embedding_route="nope"))
        is None
    )


def test_embedder_from_settings_unconfigured_returns_none() -> None:
    # No embedding_route and no default route: embeddings stay unconfigured.
    settings = _settings()
    settings = Settings(
        sandbox=settings.sandbox,
        budget=settings.budget,
        context=settings.context,
        policy=settings.policy,
        providers=settings.providers,
        default_provider="",
        memory=settings.memory,
    )
    assert embedder_from_settings(settings) is None
