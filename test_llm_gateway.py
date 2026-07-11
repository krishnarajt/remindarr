"""Unit tests for the native LLM Gateway client."""

import httpx
import pytest

from app.services.llm_gateway import LLMGatewayClient, LLMGatewayError


def _response(status: int, *, json=None, text=None) -> httpx.Response:
    request = httpx.Request("POST", "https://gateway.test/custom/chat")
    if json is not None:
        return httpx.Response(status, request=request, json=json)
    return httpx.Response(status, request=request, text=text or "")


def test_chat_uses_configurable_native_gateway_contract(monkeypatch):
    client = LLMGatewayClient(
        base_url="https://gateway.test/",
        api_key="gw-test",
        model="gemini-flash-latest",
        chat_path="custom/chat",
        max_attempts=1,
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _response(200, json={"content": "  result  "})

    monkeypatch.setattr(client._client, "post", post)
    assert client.chat("hello", system_prompt="be useful", temperature=0) == "result"
    assert calls[0][0] == "https://gateway.test/custom/chat"
    assert calls[0][1]["headers"]["X-API-Key"] == "gw-test"
    assert calls[0][1]["json"]["config"] == {
        "model": "gemini-flash-latest",
        "temperature": 0,
    }


def test_chat_retries_transient_gateway_errors(monkeypatch):
    client = LLMGatewayClient(api_key="gw-test", max_attempts=3)
    responses = iter(
        [_response(503, text="busy"), _response(429, text="slow down"), _response(200, json={"content": "ok"})]
    )
    calls = []
    monkeypatch.setattr(client._client, "post", lambda *a, **k: calls.append(1) or next(responses))
    monkeypatch.setattr("app.services.llm_gateway.time.sleep", lambda _: None)

    assert client.chat("hello") == "ok"
    assert len(calls) == 3


def test_chat_does_not_retry_auth_failure(monkeypatch):
    client = LLMGatewayClient(api_key="bad-key", max_attempts=3)
    calls = []
    monkeypatch.setattr(
        client._client,
        "post",
        lambda *a, **k: calls.append(1) or _response(401, text="invalid key"),
    )

    with pytest.raises(LLMGatewayError, match="401"):
        client.chat("hello")
    assert len(calls) == 1


def test_chat_wraps_invalid_gateway_json(monkeypatch):
    client = LLMGatewayClient(api_key="gw-test", max_attempts=1)
    monkeypatch.setattr(client._client, "post", lambda *a, **k: _response(200, text="not-json"))

    with pytest.raises(LLMGatewayError, match="invalid JSON"):
        client.chat("hello")
