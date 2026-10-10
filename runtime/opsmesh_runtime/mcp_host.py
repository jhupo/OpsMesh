"""One cancellable MCP operation, supervised by a private Docker exec channel."""

import asyncio
import json
import os
import sys
from contextlib import suppress

from .mcp_http_client import execute_request as http_request
from .mcp_process import exchange, socket_path
from .mcp_stdio_client import execute_request as stdio_request

MAX_BYTES = 16_777_216


def emit(kind: str, payload: object) -> None:
    encoded = json.dumps({"type": kind, "payload": payload}, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_BYTES:
        raise ValueError("MCP control response exceeds its limit")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


async def execute(request: dict[str, object]) -> dict[str, object]:
    payload = request["request"]
    if not isinstance(payload, dict):
        raise ValueError("MCP request must be an object")
    if request["transport"] == "stdio":
        return await stdio_request(payload)
    if request["transport"] == "http":
        return await http_request(payload)
    if request["transport"] == "managed":
        result = await exchange(socket_path(str(request["server_id"])), payload)
        if "error" in result:
            raise RuntimeError("Managed MCP call failed")
        return result
    raise ValueError("Unknown Runtime MCP transport")


async def run() -> None:
    if os.getsid(0) != os.getpid():
        os.setsid()
    reader = asyncio.StreamReader(limit=MAX_BYTES)
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    raw = await reader.readline()
    if len(raw) > MAX_BYTES:
        raise ValueError("MCP control request exceeds its limit")
    request = json.loads(raw)
    emit("ready", {"pid": os.getpid(), "operation_id": request["operation_id"]})
    operation = asyncio.create_task(execute(request))
    disconnected = asyncio.create_task(reader.read(1))
    try:
        await asyncio.wait((operation, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if not operation.done():
            operation.cancel()
        result = await operation
    except Exception as error:
        emit("error", {"code": "mcp_runtime_failed", "message": type(error).__name__})
    else:
        emit("result", result)
    finally:
        operation.cancel()
        disconnected.cancel()
        with suppress(asyncio.CancelledError):
            await operation
        with suppress(asyncio.CancelledError):
            await disconnected


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
