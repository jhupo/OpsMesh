"""One SDK run per isolated process. stdin/stdout carry only bounded control-plane RPC."""

import asyncio
import logging
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from backend.app.agents.execution.contracts import (
    AgentRunRequest,
    AgentRuntimeExecutor,
    AgentRuntimeSession,
)
from backend.app.agents.execution.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
    AgentRuntimeProviderError,
)
from backend.app.runtime.agent_host.local_sandbox import LocalSandboxExecutor
from backend.app.runtime.agent_host.processes import register
from backend.app.runtime.agent_host.proxies import RpcCancellation, RpcSession, RpcTools
from backend.app.runtime.agent_host.wire import (
    MAX_FRAME_BYTES,
    RESULT,
    STREAM_EVENT,
    RpcFrame,
    RuntimeRunInput,
)
from backend.app.runtime.contracts import SandboxBinding, SandboxSession
from backend.app.shared.security.redaction import redact_sensitive_text


class HostRpc:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        self.output = sys.stdout.buffer
        self.pending: dict[str, asyncio.Future[object]] = {}
        self.closed = asyncio.Event()

    def send(self, frame: RpcFrame) -> None:
        self.output.write(frame.encoded())
        self.output.flush()

    async def call(self, method: str, payload: dict[str, object]) -> object:
        if self.closed.is_set():
            raise ConnectionError("Runtime control channel closed")
        identity = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            self.send(RpcFrame(type="call", id=identity, method=method, payload=payload))
            return await future
        finally:
            self.pending.pop(identity, None)

    async def receive(self) -> None:
        try:
            while line := await self.reader.readline():
                frame = RpcFrame.model_validate_json(line)
                future = self.pending.get(frame.id or "")
                if frame.type != "reply" or future is None or future.done():
                    raise ValueError("Unexpected Runtime RPC reply")
                if frame.error is not None:
                    future.set_exception(RuntimeError(str(frame.error.get("code", "rpc_failed"))))
                else:
                    future.set_result(frame.payload)
        finally:
            self.closed.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Runtime control channel closed"))


class SDKCompactionLogHandler(logging.Handler):
    def __init__(self, rpc: HostRpc) -> None:
        super().__init__()
        self.rpc = rpc

    def emit(self, record: logging.LogRecord) -> None:
        message = redact_sensitive_text(record.getMessage())[:8192]
        self.rpc.send(
            RpcFrame(
                type="log",
                payload={"logger": record.name, "level": record.levelno, "message": message},
            )
        )


async def execute(executor: AgentRuntimeExecutor) -> None:
    reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES)
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    rpc = HostRpc(reader)
    logger = logging.getLogger("openai-agents.openai.compaction")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(SDKCompactionLogHandler(rpc))
    logger.propagate = False
    # Provider and subprocess diagnostics must not corrupt the RPC stream.
    sys.stdout = sys.stderr
    first = RpcFrame.model_validate_json(await reader.readline())
    if first.type != "start":
        raise ValueError("SDK host requires its frozen run input")
    wire = RuntimeRunInput.model_validate(first.payload)
    root = Path(wire.manifest.root)
    (root / ".tmp").mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ.update({"HOME": str(root), "TMPDIR": str(root / ".tmp")})
    if os.getsid(0) != os.getpid():
        os.setsid()
    process_lock = register(wire.context.run_id)
    rpc.send(
        RpcFrame(type="ready", payload={"pid": os.getpid(), "run_id": str(wire.context.run_id)})
    )
    storage = RpcSession(wire.session_id, rpc.call) if wire.session_id else None
    sandbox = SandboxBinding(
        wire.manifest,
        SandboxSession(
            str(wire.context.run_id),
            wire.manifest.root,
            "local",
            LocalSandboxExecutor(
                wire.manifest.root, wire.sandbox_timeout_seconds, wire.sandbox_max_file_bytes
            ),
            wire.persistent,
        ),
    )
    arguments = wire.model_dump(
        exclude={
            "session_id",
            "tools_enabled",
            "manifest",
            "persistent",
            "sandbox_timeout_seconds",
            "sandbox_max_file_bytes",
            "attachments",
        }
    )
    # Preserve immutable dataclasses rather than passing Pydantic's JSON mappings to SDK adapters.
    arguments.update({name: getattr(wire, name) for name in arguments})
    credential_id = wire.model_provider_credential_id
    arguments["model_provider_credential_id"] = UUID(credential_id) if credential_id else None
    request = AgentRunRequest(
        **arguments,
        session=cast(AgentRuntimeSession, storage),
        tool_executor=RpcTools(rpc.call) if wire.tools_enabled else None,
        cancellation=RpcCancellation(rpc.call),
        sandbox=sandbox,
        attachments=tuple(a.attachment() for a in wire.attachments),
        event_sink=lambda event: rpc.send(
            RpcFrame(type="event", payload=STREAM_EVENT.dump_python(event, mode="json"))
        ),
    )
    receiver = asyncio.create_task(rpc.receive(), name="runtime-control")
    runner = asyncio.create_task(executor.run(request), name="runtime-sdk")
    disconnected = asyncio.create_task(rpc.closed.wait())
    try:
        await asyncio.wait((runner, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if not runner.done():
            runner.cancel()
        result = await runner
        rpc.send(RpcFrame(type="result", payload=RESULT.dump_python(result, mode="json")))
    except AgentRuntimeProviderError as error:
        rpc.send(
            RpcFrame(
                type="error",
                error={
                    "kind": "provider",
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            )
        )
    except AgentRuntimeCancelledError:
        rpc.send(RpcFrame(type="error", error={"kind": "cancelled"}))
    except AgentRuntimePolicyError as error:
        rpc.send(
            RpcFrame(
                type="error",
                error={
                    "kind": "policy",
                    "code": error.code,
                    "message": error.message,
                    "event_type": error.event_type,
                    "metadata": error.metadata,
                    "retryable": error.retryable,
                },
            )
        )
    except Exception as error:
        rpc.send(
            RpcFrame(
                type="error",
                error={
                    "kind": "runtime",
                    "code": "sdk_host_failed",
                    "message": type(error).__name__,
                },
            )
        )
    finally:
        receiver.cancel()
        disconnected.cancel()
        with suppress(asyncio.CancelledError, ConnectionError):
            await receiver
        with suppress(asyncio.CancelledError):
            await disconnected
        process_lock.close()
