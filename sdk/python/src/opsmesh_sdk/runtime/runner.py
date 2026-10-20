"""Bounded single-invocation entry point inside the approved package environment."""

import asyncio
import contextlib
import json
import sys
from typing import Any

from opsmesh_sdk.runtime.loading import invoke
from opsmesh_sdk.transport.pipe import MAX_MESSAGE_BYTES, PipeTransport


async def run() -> None:
    sys.path.append("/opt/extension")
    output = sys.stdout
    raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
    if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
        raise ValueError("Invalid invocation envelope")
    message: dict[str, Any] = json.loads(raw)
    transport = PipeTransport()
    with contextlib.redirect_stdout(sys.stderr):
        result = await invoke(message, transport)
    payload = json.dumps({"type": "result", "result": result}, allow_nan=False)
    if len(payload.encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("Extension result exceeds the byte bound")
    output.write(payload + "\n")
    output.flush()


if __name__ == "__main__":
    asyncio.run(run())
