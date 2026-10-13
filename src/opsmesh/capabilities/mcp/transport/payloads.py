from __future__ import annotations

from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionError


def stdio_command(connection: dict[str, object]) -> list[str]:
    raw_command = connection.get("command")
    if isinstance(raw_command, str) and raw_command.strip():
        command = [raw_command.strip()]
        args = connection.get("args")
        if args is not None and (
            not isinstance(args, list) or not all(isinstance(item, str) for item in args)
        ):
            raise MCPExecutionError("MCP args must be strings", code="mcp_stdio_args_invalid")
        if isinstance(args, list):
            command.extend(args)
        return command
    raise MCPExecutionError(
        "Stdio MCP server is missing command",
        code="mcp_stdio_command_missing",
    )
