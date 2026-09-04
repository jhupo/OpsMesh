"""Run one MCP stdio tool call inside an isolated runtime.

The MCP Python SDK owns process management and protocol framing. OpsMesh only
passes a product-neutral request into this entrypoint and serializes the SDK
result back to stdout for the runtime command boundary.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from typing import cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def execute_request(request: dict[str, object]) -> dict[str, object]:
    server = _mapping(request, "server")
    tool = _mapping(request, "tool")
    command = _string(server, "command")
    args = _string_list(server, "args")
    tool_name = _string(tool, "name")
    arguments = _mapping(tool, "arguments")
    timeout_seconds = _positive_int(tool, "timeout_seconds")

    server_parameters = StdioServerParameters(command=command, args=args)
    async with (
        stdio_client(server_parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            tool_name,
            arguments=arguments,
            read_timeout_seconds=timedelta(seconds=timeout_seconds),
        )
    return cast(dict[str, object], result.model_dump(mode="json", by_alias=True, exclude_none=True))


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if len(arguments) != 1:
        print("MCP stdio SDK client requires one JSON request argument", file=sys.stderr)
        return 2
    try:
        request = json.loads(arguments[0])
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        result = asyncio.run(execute_request(request))
    except Exception as exc:
        print(f"MCP stdio SDK client failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"request field {key!r} must be an object")
    return value


def _string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"request field {key!r} must be a non-empty string")
    return value


def _string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"request field {key!r} must be a string list")
    return value


def _positive_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"request field {key!r} must be a positive integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
