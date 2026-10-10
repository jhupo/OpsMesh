"""Async Docker exec I/O. Waiting for an SDK frame never occupies an adapter thread."""

import asyncio
import struct
from typing import Any

from backend.app.runtime.agent_host.wire import MAX_FRAME_BYTES


class DockerAgentChannel:
    def __init__(self, connection: Any, client: Any) -> None:
        self.connection = connection
        self.client = client
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.buffer = bytearray()

    async def attach(self) -> None:
        # Docker SDK's documented socket=True endpoint returns SocketIO wrapping
        # the hijacked HTTP socket, as also used for ephemeral credential input.
        transport = self.connection._sock
        transport.setblocking(False)
        self.reader, self.writer = await asyncio.open_connection(sock=transport)

    async def send(self, data: bytes) -> None:
        if self.writer is None:
            raise RuntimeError("SDK control channel is not attached")
        self.writer.write(data)
        await self.writer.drain()

    async def receive(self) -> bytes:
        if self.reader is None:
            raise RuntimeError("SDK control channel is not attached")
        while True:
            end = self.buffer.find(b"\n")
            if end >= 0:
                result = bytes(self.buffer[: end + 1])
                del self.buffer[: end + 1]
                return result
            header = await self.reader.readexactly(8)
            stream, length = header[0], struct.unpack(">I", header[4:])[0]
            if length > MAX_FRAME_BYTES or stream not in {1, 2}:
                raise ValueError("SDK control channel returned an invalid Docker frame")
            chunk = await self.reader.readexactly(length)
            if stream == 1:
                self.buffer.extend(chunk)
                if len(self.buffer) > MAX_FRAME_BYTES:
                    raise ValueError("SDK control frame exceeds its transfer limit")
            # stderr contains provider diagnostics, which can include sensitive
            # request details. Only typed errors and SDK events enter durable logs.

    async def close(self) -> None:
        try:
            if self.writer is not None:
                self.writer.close()
                await self.writer.wait_closed()
        finally:
            try:
                self.connection._response.close()
            finally:
                try:
                    self.connection.close()
                finally:
                    self.client.close()
