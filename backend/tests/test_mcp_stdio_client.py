from __future__ import annotations

import asyncio

from backend.app.capabilities.mcp_adapter_payloads import stdio_sdk_request
from backend.app.runtime_manager import mcp_stdio_client


def test_stdio_sdk_client_uses_official_stdio_transport_and_session(monkeypatch) -> None:
    transport = _FakeTransport()
    session = _FakeSession()
    monkeypatch.setattr(mcp_stdio_client, "stdio_client", lambda _: transport)
    monkeypatch.setattr(mcp_stdio_client, "ClientSession", lambda *_: session)

    request = stdio_sdk_request(
        command=["mcp-server", "--stdio"],
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
        timeout_seconds=7,
    )

    result = asyncio.run(mcp_stdio_client.execute_request(request))

    assert result == {"structuredContent": {"ok": True}}
    assert session.initialized is True
    assert session.tool_call == {
        "name": "generate_image",
        "arguments": {"prompt": "mountain"},
        "timeout_seconds": 7,
    }


class _FakeTransport:
    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSession:
    initialized = False
    tool_call: dict[str, object] | None = None

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True

    async def call_tool(
        self,
        name: str,
        *,
        arguments: dict[str, object],
        read_timeout_seconds: object,
    ) -> _FakeResult:
        self.tool_call = {
            "name": name,
            "arguments": arguments,
            "timeout_seconds": read_timeout_seconds.total_seconds(),
        }
        return _FakeResult()


class _FakeResult:
    def model_dump(self, **_: object) -> dict[str, object]:
        return {"structuredContent": {"ok": True}}
