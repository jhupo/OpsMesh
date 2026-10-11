"""Native Agents SDK transport with bounded private stderr diagnostics."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agents.mcp import MCPServerStdio
from agents.mcp.server import MCPStreamTransport
from mcp.client.stdio import stdio_client
from mcp.types import LoggingMessageNotification, ServerNotification

from .diagnostics import DIAGNOSTIC_META_KEY, diagnostic_fields
from .stderr_capture import StderrCapture


class RuntimeMCPServerStdio(MCPServerStdio):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.stderr_capture = StderrCapture()
        self.remote_diagnostic: dict[str, Any] = {}
        super().__init__(*args, message_handler=self.capture_message, **kwargs)

    async def capture_message(self, message: Any) -> None:
        if isinstance(message, ServerNotification) and isinstance(
            message.root, LoggingMessageNotification
        ):
            safe = diagnostic_fields(message.root.params.data)
            if safe:
                self.remote_diagnostic = safe

    def reset_diagnostics(self) -> None:
        self.remote_diagnostic = {}
        self.stderr_capture.reset()

    def diagnostic_result(self, response: dict[str, Any]) -> dict[str, Any]:
        if response.get("isError") is not True:
            return response
        metadata = dict(response.get("_meta") or {})
        metadata[DIAGNOSTIC_META_KEY] = {
            **diagnostic_fields(metadata.get(DIAGNOSTIC_META_KEY)),
            **self.remote_diagnostic,
            "diagnostic": {"stderr": self.stderr_capture.snapshot()},
        }
        return {**response, "_meta": metadata}

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
        async with (
            self.stderr_capture.pipe() as errors,
            stdio_client(params, errlog=errors) as streams,
        ):
            yield streams
