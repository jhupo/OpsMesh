"""Native Agents SDK MCP transport with project stderr kept out of host logs."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agents.mcp import MCPServerStdio
from agents.mcp.server import MCPStreamTransport
from mcp.client.stdio import stdio_client


class RuntimeMCPServerStdio(MCPServerStdio):
    @asynccontextmanager
    async def create_streams(self) -> AsyncIterator[MCPStreamTransport]:
        # The SDK does not expose stderr routing in MCPServerStdioParams.
        with open(os.devnull, "w") as errors:
            async with stdio_client(self.params, errlog=errors) as streams:
                yield streams
