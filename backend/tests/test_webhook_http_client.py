import httpx

from backend.app.webhooks.constants import WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH
from backend.app.webhooks.http_client import HttpxWebhookHttpClient


def test_httpx_webhook_client_streams_bounded_non_success_response() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.content == b'{"event":"task.failed"}'
        assert request.headers["X-OpsMesh-Event-Id"] == "evt-1"
        return httpx.Response(
            503,
            content=b"x" * (WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH + 100),
            headers={"X-Upstream": "gateway"},
        )

    response = HttpxWebhookHttpClient(transport=httpx.MockTransport(handle)).post(
        url="https://hooks.example.test/events",
        body=b'{"event":"task.failed"}',
        headers={"X-OpsMesh-Event-Id": "evt-1"},
        timeout_seconds=5,
    )

    assert response.status_code == 503
    assert response.body == "x" * WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH
    assert response.headers["x-upstream"] == "gateway"


def test_httpx_webhook_client_does_not_follow_redirects() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://internal.example.test/secret"})

    response = HttpxWebhookHttpClient(transport=httpx.MockTransport(handle)).post(
        url="https://hooks.example.test/events",
        body=b"{}",
        headers={},
        timeout_seconds=5,
    )

    assert response.status_code == 307
    assert calls == ["https://hooks.example.test/events"]
