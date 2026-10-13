from __future__ import annotations

import re

from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionError

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ENVIRONMENT_VARIABLES = 128
_MAX_ENVIRONMENT_BYTES = 65_536


def _merge_environment(
    destination: dict[str, str],
    raw_environment: dict[object, object],
) -> None:
    for raw_name, raw_value in raw_environment.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise MCPExecutionError(
                "Hosted MCP stdio credential env must contain string names and values",
                code="mcp_stdio_credential_invalid",
            )
        _validate_environment_name(raw_name)
        if raw_name in destination:
            raise MCPExecutionError(
                "MCP stdio credentials define the same environment variable more than once",
                code="mcp_stdio_credential_conflict",
            )
        destination[raw_name] = raw_value
        _enforce_environment_limits(destination)


def _validate_environment_name(name: str) -> None:
    if not _ENVIRONMENT_NAME.fullmatch(name):
        raise MCPExecutionError(
            "MCP stdio credential environment variable name is invalid",
            code="mcp_stdio_credential_invalid",
        )


def _enforce_environment_limits(environment: dict[str, str]) -> None:
    encoded_bytes = sum(
        len(name.encode("utf-8")) + len(value.encode("utf-8"))
        for name, value in environment.items()
    )
    if len(environment) > _MAX_ENVIRONMENT_VARIABLES or encoded_bytes > _MAX_ENVIRONMENT_BYTES:
        raise MCPExecutionError(
            "MCP stdio credential environment exceeds configured limits",
            code="mcp_stdio_credential_too_large",
        )
