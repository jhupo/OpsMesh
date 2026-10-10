"""Detached Runtime command: async socket waits, explicit process-group cleanup."""

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from uuid import uuid4

from backend.app.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.runtime.agent_host.wire import RpcFrame
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.shared.concurrency import BlockingIO


@dataclass(frozen=True)
class RuntimeMcpOperation:
    docker: DockerRuntimeClient
    container_id: str
    transport: str
    request: dict[str, object] = field(repr=False)
    timeout_seconds: int
    working_dir: str = "/"
    server_id: str | None = None

    async def execute(self, io: BlockingIO) -> dict[str, object]:
        channel = await io.run(
            partial(self.docker.open_mcp_channel, self.container_id, working_dir=self.working_dir)
        )
        operation_id = uuid4().hex
        pid = None
        try:
            await channel.attach()
            await channel.send(
                json.dumps(
                    {
                        "operation_id": operation_id,
                        "transport": self.transport,
                        "server_id": self.server_id,
                        "request": self.request,
                    }
                ).encode()
                + b"\n"
            )
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    frame = RpcFrame.model_validate_json(await channel.receive())
                    if frame.type == "ready":
                        payload = frame.payload
                        if (
                            not isinstance(payload, dict)
                            or payload.get("operation_id") != operation_id
                        ):
                            raise ValueError("MCP operation identity does not match")
                        candidate = payload.get("pid")
                        if not isinstance(candidate, int) or candidate <= 1 or pid is not None:
                            raise ValueError("MCP process identity is invalid")
                        pid = candidate
                    elif frame.type == "result" and pid is not None:
                        if not isinstance(frame.payload, dict):
                            raise ValueError("MCP response must be an object")
                        return frame.payload
                    elif frame.type == "error" and pid is not None:
                        raise McpExecutionError(
                            "Runtime MCP execution failed", code="mcp_runtime_failed"
                        )
                    else:
                        raise ValueError("Unexpected Runtime MCP frame")
        finally:
            try:
                if pid is not None:
                    await io.run(
                        partial(self.docker.terminate_agent_process, self.container_id, pid)
                    )
            finally:
                with suppress(ConnectionError, BrokenPipeError):
                    await channel.close()


@dataclass(frozen=True)
class CompletedMcpOperation:
    response: dict[str, object]

    async def execute(self, io: BlockingIO) -> dict[str, object]:
        return self.response
