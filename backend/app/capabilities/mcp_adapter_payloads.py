from __future__ import annotations

import json
from typing import Any

from backend.app.capabilities.mcp_execution_types import McpExecutionError

MCP_PYTHON_SDK_PACKAGE = "mcp"
MCP_PYTHON_SDK_STDIO_ENTRYPOINT = "mcp.client.stdio.stdio_client"


def string_setting(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def string_dict_setting(payload: dict[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        str(item_key).lower(): item_value
        for item_key, item_value in value.items()
        if isinstance(item_key, str) and isinstance(item_value, str)
    }


def jsonable(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return str(value)


def stdio_sdk_request(
    *,
    command: list[str],
    tool_name: str,
    arguments: dict[str, object],
    timeout_seconds: int,
) -> dict[str, object]:
    if not command:
        raise McpExecutionError(
            "Stdio MCP server is missing command",
            code="mcp_stdio_command_missing",
        )
    return {
        "client": {
            "package": MCP_PYTHON_SDK_PACKAGE,
            "entrypoint": MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
        },
        "server": {
            "command": command[0],
            "args": command[1:],
        },
        "tool": {
            "name": tool_name,
            "arguments": arguments,
            "timeout_seconds": timeout_seconds,
        },
    }


def stdio_command(connection: dict[str, object]) -> list[str]:
    raw_command = connection.get("command")
    if isinstance(raw_command, list) and raw_command:
        command = [item for item in raw_command if isinstance(item, str) and item]
        if command:
            return command
    if isinstance(raw_command, str) and raw_command.strip():
        command = [raw_command.strip()]
        args = connection.get("args")
        if isinstance(args, list):
            command.extend(item for item in args if isinstance(item, str) and item)
        return command
    raise McpExecutionError(
        "Stdio MCP server is missing command",
        code="mcp_stdio_command_missing",
    )


def result_from_sdk_output(raw_body: bytes | str) -> dict[str, object]:
    if isinstance(raw_body, bytes):
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpExecutionError(
                "MCP stdio SDK client returned invalid UTF-8",
                code="mcp_stdio_invalid_encoding",
            ) from exc
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise McpExecutionError(
            "MCP stdio SDK client returned invalid JSON",
            code="mcp_stdio_invalid_output",
        ) from exc
    if not isinstance(body, dict):
        raise McpExecutionError(
            "MCP stdio SDK client returned an invalid result",
            code="mcp_stdio_invalid_output",
        )
    if body.get("isError") is True:
        raise McpExecutionError(
            "MCP stdio tool failed",
            code="mcp_remote_error",
        )
    structured_content = body.get("structuredContent")
    if isinstance(structured_content, dict):
        return {
            str(key): value
            for key, value in structured_content.items()
            if isinstance(key, str)
        }
    content = body.get("content")
    return {"content": content if isinstance(content, list) else []}


def result_from_sse_body(raw_body: bytes) -> dict[str, object]:
    try:
        body = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise McpExecutionError(
            "SSE MCP server returned invalid UTF-8",
            code="mcp_sse_invalid_encoding",
        ) from exc

    for data in sse_data_messages(body):
        if not data or data == "[DONE]":
            continue
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError as exc:
            raise McpExecutionError(
                "SSE MCP server returned invalid JSON",
                code="mcp_sse_invalid_json",
            ) from exc
        if not isinstance(envelope, dict):
            raise McpExecutionError(
                "SSE MCP server returned an invalid JSON-RPC envelope",
                code="mcp_sse_invalid_envelope",
            )
        error = envelope.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                "Remote MCP tool failed",
                code="mcp_remote_error",
            )
        if "result" in envelope:
            result = envelope["result"]
            return result if isinstance(result, dict) else {"result": jsonable(result)}

    raise McpExecutionError(
        "SSE MCP server did not return a result",
        code="mcp_sse_no_result",
    )


def sse_data_messages(body: str) -> list[str]:
    messages: list[str] = []
    data_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                messages.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
    if data_lines:
        messages.append("\n".join(data_lines))
    return messages
