"""Run one MCP stdio tool call inside an isolated runtime."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import timedelta
from importlib.metadata import version
from typing import cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_SDK_PACKAGE = "mcp"
MCP_SDK_STDIO_ENTRYPOINT = "mcp.client.stdio.stdio_client"
RUNTIME_CONTRACT_VERSION = 1
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ENVIRONMENT_VARIABLES = 128
_MAX_ENVIRONMENT_BYTES = 65_536


async def execute_request(request: dict[str, object]) -> dict[str, object]:
    validate_request_contract(request)
    server = _mapping(request, "server")
    tool = _mapping(request, "tool")
    server_parameters = StdioServerParameters(
        command=_string(server, "command"),
        args=_string_list(server, "args"),
        env=_optional_string_mapping(server, "env"),
    )
    timeout_seconds = _positive_int(tool, "timeout_seconds")
    async with asyncio.timeout(timeout_seconds):
        async with (
            stdio_client(server_parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                _string(tool, "name"),
                arguments=_mapping(tool, "arguments"),
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            )
    return cast(dict[str, object], result.model_dump(mode="json", by_alias=True, exclude_none=True))


def validate_request_contract(request: dict[str, object]) -> None:
    contract_version = request.get("contract_version")
    if (
        not isinstance(contract_version, int)
        or isinstance(contract_version, bool)
        or contract_version != RUNTIME_CONTRACT_VERSION
    ):
        raise ValueError("unsupported MCP stdio request contract version")
    client = _mapping(request, "client")
    if (
        client.get("package") != MCP_SDK_PACKAGE
        or client.get("entrypoint") != MCP_SDK_STDIO_ENTRYPOINT
    ):
        raise ValueError("unsupported MCP stdio SDK contract")
    server = _mapping(request, "server")
    _string(server, "command")
    _string_list(server, "args")
    _optional_string_mapping(server, "env")
    tool = _mapping(request, "tool")
    _string(tool, "name")
    _mapping(tool, "arguments")
    _positive_int(tool, "timeout_seconds")


def capability_report() -> dict[str, object]:
    return {
        "status": "ready",
        "contract_version": RUNTIME_CONTRACT_VERSION,
        "sdk_package": MCP_SDK_PACKAGE,
        "sdk_version": version(MCP_SDK_PACKAGE),
        "stdio_client": "available",
        "client_session": "available",
    }


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if arguments == ["--check"]:
        print(json.dumps(capability_report(), ensure_ascii=False, separators=(",", ":")))
        return 0
    if arguments == ["--request-stdin"]:
        raw_request = sys.stdin.read()
    else:
        print("MCP stdio SDK client requires --request-stdin", file=sys.stderr)
        return 2
    try:
        request = json.loads(raw_request)
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


def _optional_string_mapping(
    payload: dict[str, object],
    key: str,
) -> dict[str, str] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict) or any(
        not isinstance(item_key, str) or not isinstance(item_value, str)
        for item_key, item_value in value.items()
    ):
        raise ValueError(f"request field {key!r} must be a string mapping")
    if any(not _ENVIRONMENT_NAME.fullmatch(item_key) for item_key in value):
        raise ValueError(f"request field {key!r} contains an invalid environment name")
    encoded_bytes = sum(
        len(item_key.encode("utf-8")) + len(item_value.encode("utf-8"))
        for item_key, item_value in value.items()
    )
    if len(value) > _MAX_ENVIRONMENT_VARIABLES or encoded_bytes > _MAX_ENVIRONMENT_BYTES:
        raise ValueError(f"request field {key!r} exceeds environment limits")
    return value


def _positive_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"request field {key!r} must be a positive integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
