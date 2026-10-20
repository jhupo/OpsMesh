"""Private resident process and stdio connector, with a separate channel per invocation."""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from opsmesh_sdk.errors import TransportError
from opsmesh_sdk.transport.contracts import (
    StreamChunk,
    TransportContext,
    TransportRequest,
    TransportResponse,
)
from opsmesh_sdk.transport.pipe import MAX_MESSAGE_BYTES

SOCKET = "/tmp/opsmesh-worker.sock"


class SocketTransport:
    def __init__(
        self, writer: asyncio.StreamWriter, answers: asyncio.Queue[dict[str, Any]]
    ) -> None:
        self.writer, self.answers = writer, answers
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def send(self, request: TransportRequest, context: TransportContext) -> TransportResponse:
        context.scope.ensure_active()
        if request.stream or request.content is not None:
            raise TransportError("Resident host RPC accepts bounded JSON only")
        async with self.lock:
            self.sequence += 1
            if self.sequence > 1024:
                raise TransportError("Invocation host RPC occurrence bound exceeded")
            await write(
                self.writer,
                {
                    "type": "request",
                    "id": self.sequence,
                    "request": request.model_dump(mode="json"),
                },
            )
            answer = await self.answers.get()
            if answer.get("id") != self.sequence:
                raise TransportError("Host response occurrence differs")
            result = TransportResponse.model_validate(
                {k: v for k, v in answer.items() if k != "id"}
            )
            if result.status >= 400:
                raise TransportError("The host denied or could not confirm this operation")
            return result

    async def stream(
        self,
        request: TransportRequest,
        context: TransportContext,
    ) -> AsyncIterator[StreamChunk]:
        raise TransportError("Resident host streams are not supported")
        yield StreamChunk()  # pragma: no cover

    async def aclose(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()


async def write(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    payload = json.dumps(message, allow_nan=False).encode() + b"\n"
    if len(payload) > MAX_MESSAGE_BYTES:
        raise TransportError("Resident protocol message exceeds its byte bound")
    writer.write(payload)
    await writer.drain()


async def read(reader: asyncio.StreamReader) -> dict[str, Any]:
    raw = await reader.readline()
    if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE_BYTES:
        raise TransportError("Resident protocol ended without a bounded message")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TransportError("Resident protocol messages must be objects")
    return value


async def serve(namespace: str, instance_id: str, concurrency: int) -> None:
    # Load the approved package in the isolated resident process.
    sys.path.append("/opt/extension")
    from opsmesh_sdk.runtime.loading import discover, invoke

    lifecycle = discover(namespace, None)
    if (
        lifecycle.manifest.execution.mode != "managed_worker"
        or lifecycle.manifest.execution.max_concurrency != concurrency
    ):
        raise ValueError("Resident code differs from its approved execution policy")
    if lifecycle.worker_startup:
        await lifecycle.worker_startup()
    active: dict[str, asyncio.Task[Any]] = {}
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        operation: asyncio.Task[dict[str, Any]] | None = None
        pump: asyncio.Task[None] | None = None
        identifier = ""
        try:
            message = await asyncio.wait_for(read(reader), 5)
            subject = message["subject"]
            identifier = str(UUID(subject["invocation_id"]))
            if (
                message["action"] != "invoke"
                or message["namespace"] != namespace
                or subject["instance_id"] != instance_id
                or identifier in active
                or len(active) >= concurrency
                or stopping.is_set()
            ):
                raise TransportError("Resident invocation identity or capacity is invalid")
            answers: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)

            async def receive() -> None:
                while True:
                    await answers.put(await read(reader))

            operation = asyncio.create_task(invoke(message, SocketTransport(writer, answers)))
            active[identifier] = operation
            pump = asyncio.create_task(receive())
            async with asyncio.timeout(min(float(message["deadline_seconds"]), 3600)):
                done, _ = await asyncio.wait({operation, pump}, return_when=asyncio.FIRST_COMPLETED)
                if pump in done:
                    await pump
                    raise TransportError("Resident delegation disconnected")
                await write(writer, {"type": "result", "result": operation.result()})
        except (Exception, asyncio.CancelledError):
            # A timeout/disconnect is not proof that an external write did not happen.
            with contextlib.suppress(Exception):
                await write(writer, {"type": "result", "result": {"needs_attention": True}})
        finally:
            if identifier and active.get(identifier) is operation:
                active.pop(identifier, None)
            for task in (operation, pump):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (operation, pump) if t), return_exceptions=True)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    try:
        server = await asyncio.start_unix_server(connected, SOCKET, limit=MAX_MESSAGE_BYTES + 1)
        os.chmod(SOCKET, 0o600)
        async with server:
            await stopping.wait()
    finally:
        for task in list(active.values()):
            task.cancel()
        await asyncio.gather(*list(active.values()), return_exceptions=True)
        if lifecycle.worker_shutdown:
            await asyncio.wait_for(lifecycle.worker_shutdown(), 5)


async def connect() -> None:
    reader, writer = await asyncio.open_unix_connection(SOCKET, limit=MAX_MESSAGE_BYTES + 1)

    async def outbound() -> None:
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_MESSAGE_BYTES + 1)
            if not raw:
                return
            if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                raise TransportError("Invalid resident connector input")
            writer.write(raw)
            await writer.drain()

    async def inbound() -> None:
        while True:
            raw = await reader.readline()
            if not raw:
                return
            if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                raise TransportError("Invalid resident connector output")
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    tasks = [asyncio.create_task(outbound()), asyncio.create_task(inbound())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        writer.close()
        await writer.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--namespace")
    parser.add_argument("--instance")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    if args.connect:
        asyncio.run(connect())
    else:
        if not args.namespace or not args.instance or not 1 <= args.concurrency <= 64:
            parser.error("A bounded deployment identity is required")
        UUID(args.instance)
        # Package print/log output cannot corrupt private protocol channels.
        with contextlib.redirect_stdout(sys.stderr):
            asyncio.run(serve(args.namespace, args.instance, args.concurrency))


if __name__ == "__main__":
    main()
