"""Official SDK stdio client lifecycle and transient credential handling."""

import asyncio
import sys
from pathlib import Path

from agents.mcp import MCPServerStdio


def test_stdio_native_client_uses_typed_result_and_transient_environment():
    async def exercise():
        async with MCPServerStdio(
            params={
                "command": sys.executable,
                "args": [str(Path("tests/fixtures/mcp_stdio_server.py").resolve())],
                "env": {"MCP_BUSINESS_KEY": "private-canary"},
            },
            failure_error_function=None,
        ) as client:
            result = await client.call_tool("echo", {"message": "native-ready"})
            assert result.structured_content == {"message": "native-ready"}
            configured = await client.call_tool(
                "environment_configured", {"name": "MCP_BUSINESS_KEY"}
            )
            assert configured.structured_content == {"configured": True}
            assert "private-canary" not in str(result) + str(configured)

    asyncio.run(exercise())
