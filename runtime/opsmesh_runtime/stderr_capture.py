"""Drain SDK project stderr without storing untrusted free-form text."""

import asyncio
import hashlib
import json
import os
from collections import deque
from contextlib import asynccontextmanager, suppress
from typing import Any

from .diagnostics import diagnostic_fields


class StderrCapture:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.bytes = 0
        self.hash = hashlib.sha256()
        self.records: deque[dict[str, Any]] = deque(maxlen=32)
        self.dropped = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "sha256": self.hash.hexdigest(),
            "records_dropped": self.dropped,
            "records": list(self.records),
        }

    def record(self, line: bytes) -> None:
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            value = None
        safe = diagnostic_fields(value)
        if not safe:
            self.dropped += 1
        else:
            if len(self.records) == self.records.maxlen:
                self.dropped += 1
            self.records.append(safe)

    async def drain(self, reader: asyncio.StreamReader) -> None:
        pending = bytearray()
        oversized = False
        while chunk := await reader.read(4096):
            self.bytes += len(chunk)
            self.hash.update(chunk)
            for part in chunk.splitlines(keepends=True):
                if not oversized:
                    pending.extend(part)
                    if len(pending) > 4096:
                        pending.clear()
                        oversized = True
                if part.endswith(b"\n"):
                    if oversized:
                        self.dropped += 1
                    else:
                        self.record(bytes(pending))
                    pending.clear()
                    oversized = False
        if pending:
            self.record(bytes(pending))
        elif oversized:
            self.dropped += 1

    @asynccontextmanager
    async def pipe(self):
        if os.name == "nt":
            import msvcrt
            from asyncio.windows_utils import PipeHandle, pipe

            # Windows Proactor requires an overlapped read handle, unlike a POSIX FD.
            read_handle, write_handle = pipe(overlapped=(True, False))
            source = PipeHandle(read_handle)
            errors = os.fdopen(msvcrt.open_osfhandle(write_handle, os.O_WRONLY), "w")
        else:
            read_fd, write_fd = os.pipe()
            source = os.fdopen(read_fd, "rb", buffering=0)
            errors = os.fdopen(write_fd, "w")
        with source, errors:
            reader = asyncio.StreamReader()
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), source
            )
            task = asyncio.create_task(self.drain(reader))
            try:
                yield errors
            finally:
                errors.close()
                try:
                    async with asyncio.timeout(1):
                        await asyncio.shield(task)
                except TimeoutError:
                    task.cancel()
                finally:
                    transport.close()
                    with suppress(asyncio.CancelledError):
                        await task
