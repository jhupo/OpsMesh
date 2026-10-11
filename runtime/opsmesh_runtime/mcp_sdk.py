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
        # Its default environment also omits the execution proxy and temp scope.
        # Propagate only these supervised variables, never the host environment.
        scoped = {
            key: os.environ[key]
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
                "TMPDIR",
            )
            if key in os.environ
        }
        params = self.params.model_copy(update={"env": {**(self.params.env or {}), **scoped}})
        with open(os.devnull, "w") as errors:
            async with stdio_client(params, errlog=errors) as streams:
                yield streams
