"""Remote MCP transport remains the official SDK HTTP implementation."""

import asyncio

import pytest

from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionError
from opsmesh.capabilities.mcp.transport.remote import list_remote_mcp_tools_async
from tests.test_native_mcp_protocol import native_mcp_url as protocol_fixture

native_mcp_url = protocol_fixture


def test_remote_http_discovery_uses_official_sdk(native_mcp_url):
    tools = asyncio.run(
        list_remote_mcp_tools_async(
            url=native_mcp_url, headers={}, timeout_seconds=5, transport="http"
        )
    )
    assert [item["name"] for item in tools] == ["echo"]


def test_remote_connection_failure_remains_explicit():
    with pytest.raises(MCPExecutionError):
        asyncio.run(
            list_remote_mcp_tools_async(
                url="http://127.0.0.1:1/mcp", headers={}, timeout_seconds=0.2, transport="http"
            )
        )
