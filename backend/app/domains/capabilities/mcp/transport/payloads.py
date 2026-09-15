from __future__ import annotations

import json

from backend.app.domains.capabilities.mcp.execution.contracts import McpExecutionError

MCP_PYTHON_SDK_PACKAGE = "mcp"
MCP_PYTHON_SDK_STDIO_ENTRYPOINT = "mcp.client.stdio.stdio_client"
MCP_STDIO_CONTRACT_VERSION = 1


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


def stdio_sdk_request(
    *,
    command: list[str],
    tool_name: str,
    arguments: dict[str, object],
    timeout_seconds: int,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    if not command:
        raise McpExecutionError(
            "Stdio MCP server is missing command",
            code="mcp_stdio_command_missing",
        )
    server: dict[str, object] = {
        "command": command[0],
        "args": command[1:],
    }
    if environment:
        server["env"] = dict(environment)
    return {
        "contract_version": MCP_STDIO_CONTRACT_VERSION,
        "client": {
            "package": MCP_PYTHON_SDK_PACKAGE,
            "entrypoint": MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
        },
        "server": server,
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


def capability_report_from_sdk_output(raw_body: bytes | str) -> dict[str, object]:
    if isinstance(raw_body, bytes):
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpExecutionError(
                "MCP stdio SDK capability probe returned invalid UTF-8",
                code="mcp_stdio_runtime_not_ready",
            ) from exc
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise McpExecutionError(
            "MCP stdio SDK capability probe returned invalid JSON",
            code="mcp_stdio_runtime_not_ready",
        ) from exc
    if not isinstance(body, dict):
        raise McpExecutionError(
            "MCP stdio SDK capability probe returned an invalid report",
            code="mcp_stdio_runtime_not_ready",
        )
    return body

