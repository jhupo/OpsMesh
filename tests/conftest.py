import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def dingtalk_sdk_transport(monkeypatch):
    """Exercise native SDK serialization against each test's HTTP transport."""
    from contextvars import ContextVar

    from darabonba.core import DaraCore
    from Tea.response import TeaResponse

    from opsmesh.plugins.channels.dingtalk.client import DingTalkClient

    transport = ContextVar("dingtalk_test_transport")
    initialize = DingTalkClient.__init__

    def initialize_client(self, settings, http):
        initialize(self, settings, http)
        transport.set(http)

    async def send(request, runtime_option=None):
        http = transport.get()
        assert http is not None, "Test must supply an SDK HTTP transport"
        response = await http.request(
            request.method,
            DaraCore.compose_url(request),
            headers=request.headers,
            content=request.body,
        )
        result = TeaResponse()
        result.status_code = response.status_code
        result.status_message = response.reason_phrase
        result.headers = dict(response.headers)
        result.body = response.content
        return result

    monkeypatch.setattr(DingTalkClient, "__init__", initialize_client)
    monkeypatch.setattr(DaraCore, "async_do_action", send)
