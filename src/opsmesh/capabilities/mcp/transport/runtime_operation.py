"""Detached Runtime command: async socket waits, explicit process-group cleanup."""

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from time import monotonic
from uuid import uuid4

from opsmesh.capabilities.mcp.execution.contracts import McpExecutionError
from opsmesh.capabilities.mcp.execution.diagnostics import DIAGNOSTIC_META_KEY, normalize_failure
from opsmesh.runtime.agent_host.wire import RpcFrame
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.instances.execution_identity import RuntimeExecutionIdentity
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.telemetry.request_context import current_evidence_context


@dataclass(frozen=True)
class RuntimeMcpOperation:
    docker: DockerRuntimeClient
    container_id: str
    transport: str
    request: dict[str, object] = field(repr=False)
    timeout_seconds: int
    identity: RuntimeExecutionIdentity
    working_dir: str = "/"
    server_id: str | None = None
    metadata: dict[str, str] = field(default_factory=current_evidence_context)

    async def execute(self, io: BlockingIO) -> dict[str, object]:
        started = monotonic()
        channel = await io.run(
            partial(
                self.docker.open_mcp_channel,
                self.container_id,
                working_dir=self.working_dir,
                identity=self.identity,
            )
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
                        "meta": {**self.metadata, "operation_id": operation_id},
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
                        if frame.payload.get("isError") is True:
                            metadata = dict(frame.payload.get("_meta") or {})
                            details = metadata.get(DIAGNOSTIC_META_KEY)
                            metadata[DIAGNOSTIC_META_KEY] = {
                                **(details if isinstance(details, dict) else {}),
                                "operation_id": operation_id,
                            }
                            return {**frame.payload, "_meta": metadata}
                        return frame.payload
                    elif frame.type == "error" and pid is not None:
                        raise McpExecutionError(
                            "Runtime MCP execution failed",
                            code="mcp_runtime_failed",
                            diagnostic={
                                **normalize_failure(
                                    frame.payload,
                                    code="mcp_runtime_failed",
                                    message="Runtime MCP execution failed",
                                ),
                                "operation_id": operation_id,
                            },
                        )
                    else:
                        raise ValueError("Unexpected Runtime MCP frame")
        finally:
            logging.getLogger(__name__).info(
                "Runtime MCP operation finished",
                extra={
                    **self.metadata,
                    "operation_id": operation_id,
                    "duration_ms": max(0, int((monotonic() - started) * 1000)),
                    "phase": "runtime_operation",
                },
            )
            try:
                if pid is not None:
                    await io.run(
                        partial(
                            self.docker.terminate_agent_process,
                            self.container_id,
                            pid,
                            identity=self.identity,
                        )
                    )
            finally:
                with suppress(ConnectionError, BrokenPipeError):
                    await channel.close()


@dataclass(frozen=True)
class CompletedMcpOperation:
    response: dict[str, object]

    async def execute(self, io: BlockingIO) -> dict[str, object]:
        return self.response
