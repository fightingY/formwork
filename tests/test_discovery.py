from __future__ import annotations

import httpx
import pytest

from minicc.core import discovery as discovery_module
from minicc.core.discovery import DISCOVERY_FAILED, ModelInfo, discover_models
from minicc.core.provider import AUTH, RATE_LIMIT, SERVER, TRANSPORT, ProviderError


def _mock_http_client(monkeypatch, handler):
    """Route every ``httpx.Client`` that ``discover_models`` creates through a MockTransport.

    Mirrors the provider test's approach: discovery builds its own client lazily, so the
    deterministic interception point is the ``Client`` constructor in the discovery namespace.
    """
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        discovery_module.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    return transport


def test_discovers_model_ids_and_optional_capacity(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "a", "context_window": 131072, "max_tokens": 8192},
                    {"id": "b", "context_length": 32768},
                    {"id": "c"},
                ]
            },
        )

    _mock_http_client(monkeypatch, handler)
    models = discover_models("https://example.test/v1", "key")

    assert models[0] == ModelInfo(id="a", context_window=131072, max_output_tokens=8192)
    assert models[1] == ModelInfo(id="b", context_window=32768)
    assert models[2] == ModelInfo(id="c")


def test_sends_bearer_and_joins_path_base(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["extra"] = request.headers.get("x-extra", "")
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    _mock_http_client(monkeypatch, handler)
    discover_models(
        "https://example.test/compatible-mode/v1/",
        "sk-test",
        headers={"X-Extra": "1"},
    )

    assert seen["path"] == "/compatible-mode/v1/models"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["extra"] == "1"


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_are_auth(monkeypatch, status) -> None:
    _mock_http_client(monkeypatch, lambda request: httpx.Response(status, json={"error": "no"}))

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key")

    assert exc_info.value.failure.code == AUTH
    assert exc_info.value.failure.status == status


def test_429_is_rate_limit(monkeypatch) -> None:
    _mock_http_client(monkeypatch, lambda request: httpx.Response(429, json={"error": "slow"}))

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key")

    assert exc_info.value.failure.code == RATE_LIMIT


def test_5xx_is_server(monkeypatch) -> None:
    _mock_http_client(monkeypatch, lambda request: httpx.Response(503, json={"error": "boom"}))

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key")

    assert exc_info.value.failure.code == SERVER


def test_malformed_json_is_discovery_failed(monkeypatch) -> None:
    _mock_http_client(monkeypatch, lambda request: httpx.Response(200, content=b"not json"))

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key")

    assert exc_info.value.failure.code == DISCOVERY_FAILED


def test_missing_data_array_is_discovery_failed(monkeypatch) -> None:
    _mock_http_client(monkeypatch, lambda request: httpx.Response(200, json={"models": []}))

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key")

    assert exc_info.value.failure.code == DISCOVERY_FAILED


def test_skips_malformed_and_idless_entries(monkeypatch) -> None:
    _mock_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"data": ["just-a-string", {"id": None}, {"id": "ok"}]}
        ),
    )

    assert [m.id for m in discover_models("https://example.test/v1", "key")] == ["ok"]


def test_oversized_body_fails_cleanly(monkeypatch) -> None:
    _mock_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [{"id": "x" * 100_000}]}),
    )

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key", max_bytes=64)

    assert exc_info.value.failure.code == DISCOVERY_FAILED


def test_transport_error_is_transport(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_http_client(monkeypatch, handler)

    with pytest.raises(ProviderError) as exc_info:
        discover_models("https://example.test/v1", "key")

    assert exc_info.value.failure.code == TRANSPORT