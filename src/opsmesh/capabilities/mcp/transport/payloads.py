from __future__ import annotations

from opsmesh.capabilities.mcp.execution.contracts import McpExecutionError

MCP_PYTHON_SDK_PACKAGE = "openai-agents"
MCP_PYTHON_SDK_STDIO_ENTRYPOINT = "agents.mcp.MCPServerStdio"
MCP_STDIO_CONTRACT_VERSION = 2


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
    if isinstance(raw_command, str) and raw_command.strip():
        command = [raw_command.strip()]
        args = connection.get("args")
        if args is not None and (
            not isinstance(args, list) or not all(isinstance(item, str) for item in args)
        ):
            raise McpExecutionError("MCP args must be strings", code="mcp_stdio_args_invalid")
        if isinstance(args, list):
            command.extend(args)
        return command
    raise McpExecutionError(
        "Stdio MCP server is missing command",
        code="mcp_stdio_command_missing",
    )
