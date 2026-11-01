"""Explicit remote or trusted-host MCP execution with runtime secret resolution."""

import asyncio
import importlib
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.capabilities.api import MCPServerDefinition, ToolAuthorization, ToolDefinition

SecretResolver = Callable[[str], Awaitable[str]]


class MCPServerRegistry:
    def __init__(self, database: AsyncDatabase) -> None:
        self._records = VersionedRecordStore(database, "capability_mcp")
        self.availability = PersistentVersionAvailability(database, "capability_mcp")

    async def register(self, definition: MCPServerDefinition) -> MCPServerDefinition:
        validate_definition(definition)
        ref = f"{definition.name}@{definition.version}"
        prior = await self._records.get(ref)
        if prior is not None:
            if prior.data != definition.model_dump(mode="json"):
                raise OpsMeshError("CONFLICT", "MCP versions are immutable")
            return MCPServerDefinition.model_validate(prior.data)
        await self._records.create(ref, definition.model_dump(mode="json"))
        return definition

    async def list(self) -> tuple[MCPServerDefinition, ...]:
        return tuple(
            MCPServerDefinition.model_validate(row.data) for row in await self._records.list()
        )

    async def get(self, ref: str, *, include_disabled: bool = False) -> MCPServerDefinition:
        if not include_disabled:
            await self.availability.require_enabled(ref)
        record = await self._records.get(ref)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "MCP definition not found")
        return MCPServerDefinition.model_validate(record.data)


def validate_definition(definition: MCPServerDefinition) -> None:
    if not definition.name or not definition.version:
        raise OpsMeshError("CONFIG_INVALID", "MCP identity is required")
    if definition.transport in {"streamable_http", "sse"}:
        url = urlsplit(definition.url or "")
        if (
            definition.execution_location != "remote"
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or definition.command
            or definition.environment_secret_refs
            or definition.cwd
            or definition.args
        ):
            raise OpsMeshError(
                "CONFIG_INVALID", "Remote MCP requires an HTTP URL and remote location"
            )
    elif (
        definition.execution_location != "host"
        or not definition.command
        or definition.url
        or definition.header_secret_refs
    ):
        raise OpsMeshError("CONFIG_INVALID", "stdio MCP requires an explicit host command")


class MCPServerConnector:
    def __init__(
        self,
        resolve_secret: SecretResolver,
        authorize: ToolAuthorization,
        *,
        trusted_stdio: bool = False,
        registry: MCPServerRegistry | None = None,
    ) -> None:
        self._resolve_secret = resolve_secret
        self._authorize = authorize
        self._trusted_stdio = trusted_stdio
        self._registry = registry

    async def discover(
        self, definition: MCPServerDefinition, *, principal_id: str
    ) -> tuple[ToolDefinition, ...]:
        validate_definition(definition)
        if self._registry is not None:
            await self._registry.get(f"{definition.name}@{definition.version}")
        await self._authorize(principal_id, f"mcp:{definition.name}@{definition.version}:discover")
        if not definition.allowed_tools:
            return ()
        if definition.transport == "stdio" and not self._trusted_stdio:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Host stdio execution is not enabled")
        try:
            result = await self._invoke(definition, None, {})
            return tuple(ToolDefinition.model_validate(item) for item in result)
        except OpsMeshError:
            raise
        except Exception as error:
            raise OpsMeshError("MCP_DISCOVERY_FAILED", "MCP tool discovery failed") from error

    async def call(
        self,
        definition: MCPServerDefinition,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
    ) -> Any:
        validate_definition(definition)
        if self._registry is not None:
            await self._registry.get(f"{definition.name}@{definition.version}")
        await self._authorize(
            principal_id, f"mcp:{definition.name}@{definition.version}:{tool_name}"
        )
        if not definition.allowed_tools or tool_name not in definition.allowed_tools:
            raise OpsMeshError("AUTHORIZATION_DENIED", "MCP tool is not in the allowlist")
        if definition.transport == "stdio" and not self._trusted_stdio:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Host stdio execution is not enabled")
        try:
            return await self._invoke(definition, tool_name, arguments)
        except OpsMeshError:
            raise
        except TimeoutError as error:
            raise OpsMeshError("TOOL_TIMEOUT", "MCP tool call exceeded its timeout") from error
        except Exception as error:
            raise OpsMeshError(
                "TOOL_EXECUTION_FAILED", "MCP transport or tool call failed"
            ) from error

    async def _invoke(
        self, definition: MCPServerDefinition, tool_name: str | None, arguments: dict[str, Any]
    ) -> Any:
        try:
            mcp = importlib.import_module("mcp")
        except ImportError as error:
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED", "MCP client dependency is not installed"
            ) from error
        async with asyncio.timeout(definition.timeout_seconds), AsyncExitStack() as stack:
            if definition.transport == "stdio":
                if definition.cwd and not Path(definition.cwd).is_dir():
                    raise OpsMeshError("CONFIG_INVALID", "MCP working directory does not exist")
                env = {
                    key: await self._resolve_secret(ref)
                    for key, ref in definition.environment_secret_refs.items()
                }
                parameters = mcp.StdioServerParameters(
                    command=definition.command,
                    args=list(definition.args),
                    env=env,
                    cwd=definition.cwd,
                )
                transport = importlib.import_module("mcp.client.stdio").stdio_client(parameters)
                reader, writer = await stack.enter_async_context(transport)
            else:
                headers = {
                    key: await self._resolve_secret(ref)
                    for key, ref in definition.header_secret_refs.items()
                }
                if definition.transport == "sse":
                    sse = importlib.import_module("mcp.client.sse")
                    transport = sse.sse_client(
                        definition.url,
                        headers=headers,
                        timeout=definition.timeout_seconds,
                        sse_read_timeout=definition.timeout_seconds,
                    )
                    reader, writer = await stack.enter_async_context(transport)
                else:
                    reader, writer = await self._http_streams(stack, definition, headers)
            session = await stack.enter_async_context(mcp.ClientSession(reader, writer))
            await session.initialize()
            if tool_name is None:
                return await self._discover_tools(session, mcp, definition)
            result = await session.call_tool(tool_name, arguments)
            payload = result.model_dump(mode="json", by_alias=True)
            if payload.get("isError"):
                raise OpsMeshError("TOOL_EXECUTION_FAILED", "MCP server returned an error")
            if "content" not in payload:
                raise OpsMeshError(
                    "FEATURE_UNSUPPORTED", "MCP server requires an unsupported continuation"
                )
            return payload

    async def _http_streams(
        self, stack: AsyncExitStack, definition: MCPServerDefinition, headers: dict[str, str]
    ) -> tuple[Any, Any]:
        http_module = importlib.import_module("mcp.client.streamable_http")
        if hasattr(http_module, "streamable_http_client"):
            client = await stack.enter_async_context(
                http_module.create_mcp_http_client(headers=headers)
            )
            transport = http_module.streamable_http_client(definition.url, http_client=client)
        else:
            transport = http_module.streamablehttp_client(definition.url, headers=headers)
        reader, writer, _ = await stack.enter_async_context(transport)
        return reader, writer

    async def _discover_tools(
        self, session: Any, mcp: Any, definition: MCPServerDefinition
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(100):
            if cursor is None:
                response = await session.list_tools()
            elif hasattr(mcp.types, "PaginatedRequestParams"):
                response = await session.list_tools(
                    params=mcp.types.PaginatedRequestParams(cursor=cursor)
                )
            else:
                response = await session.list_tools(cursor=cursor)
            payload = response.model_dump(mode="json", by_alias=True)
            for tool in payload["tools"]:
                if tool["name"] in definition.allowed_tools:
                    tools.append(
                        ToolDefinition(
                            name=tool["name"],
                            version=definition.version,
                            description=tool.get("description") or "",
                            input_schema=tool["inputSchema"],
                            code_ref=f"mcp:{definition.name}@{definition.version}:{tool['name']}",
                            timeout_seconds=definition.timeout_seconds,
                        ).model_dump(mode="json")
                    )
            cursor = payload.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                break
            seen.add(cursor)
        raise OpsMeshError("MCP_DISCOVERY_FAILED", "MCP tool pagination did not terminate")
