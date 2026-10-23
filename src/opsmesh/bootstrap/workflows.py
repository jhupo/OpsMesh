"""Host adapters for workflow engines and installed plugin/MCP tool resources."""

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import UUID, uuid4

from agents.mcp import MCPServer
from fastapi import FastAPI
from opsmesh_sdk.identity import AuthorizationRequest, InvocationSource, Principal
from opsmesh_sdk.registration import InvocationContext, resolve_config, tool_invocation
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.plugins.mcp import connect_plugin_mcp
from opsmesh.agents.plugins.registry import (
    PluginRegistry,
    authorize_registered_tool,
    authorize_tool_policy,
    invoke_registered_tool,
)
from opsmesh.execution.instances.access import current_user
from opsmesh.execution.mcp import AuthorizedMCP
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.contracts import ToolBinding
from opsmesh.workflows.engines.registry import EngineRegistry
from opsmesh.workflows.engines.temporal.engine import TemporalWorkflowEngine


class WorkflowTools:
    def __init__(
        self, sessions: sessionmaker[Session], *, plugins: Callable[[], PluginRegistry]
    ) -> None:
        self.sessions, self.plugins = sessions, plugins

    def actor(self, actor: CurrentUser) -> CurrentUser:
        with self.sessions.begin() as session:
            return current_user(session, actor.user_id)

    def plugin_descriptor(
        self, registry: PluginRegistry, actor: CurrentUser, binding: ToolBinding, operation: str
    ) -> dict[str, object]:
        tool = authorize_registered_tool(registry, binding.resource_id, operation, actor)
        context = registry.plugins[binding.resource_id]
        defaults = context.config.get("tools", {})
        if not isinstance(defaults, dict) or not isinstance(defaults.get(operation, {}), dict):
            raise InvalidArgumentError("Installed tool configuration must be an object")
        config = resolve_config(
            tool.registration.config_fields, {**defaults.get(operation, {}), **binding.config}
        )
        return {
            **registry.describe_tool(binding.resource_id, operation).model_dump(mode="json"),
            "plugin_version": context.manifest.version,
            "config_digest": payload_digest(dict(config)),
        }

    @asynccontextmanager
    async def mcp(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        source: InvocationSource,
    ) -> AsyncIterator[tuple[MCPServer, str, str]]:
        actor = self.actor(actor)
        registry = self.plugins()
        for context in registry.plugins.values():
            for name, registration in context.mcps.items():
                if binding.resource_id != f"{context.manifest.id}/mcp/{name}":
                    continue
                if registration.admin_only and not actor.has_platform_role("admin"):
                    raise ForbiddenError("This MCP resource requires an administrator")
                defaults = context.config.get("mcp", {})
                if not isinstance(defaults, dict) or not isinstance(defaults.get(name, {}), dict):
                    raise InvalidArgumentError("Installed MCP configuration must be an object")
                config = resolve_config(
                    registration.config_fields, {**defaults.get(name, {}), **binding.config}
                )
                if registration.authorize is not None:
                    decision = registration.authorize(
                        AuthorizationRequest(
                            Principal(user_id=actor.user_id, roles=actor.platform_roles),
                            "execute",
                            binding.resource_id + "/connect",
                            source,
                        )
                    )
                    if inspect.isawaitable(decision):
                        decision = await decision
                    if decision is not True:
                        raise ForbiddenError("MCP connection policy denied this caller")
                async with AsyncExitStack() as lifespan, asyncio.timeout(300):
                    invocation = InvocationContext(
                        uuid4(),
                        actor.user_id,
                        actor.has_platform_role("admin"),
                        config,
                        binding.resource_id,
                        Principal(user_id=actor.user_id, roles=actor.platform_roles),
                        source,
                    )
                    with tool_invocation(invocation):
                        native = await connect_plugin_mcp(
                            context,
                            registration,
                            binding.config,
                            {"require_approval": "always"},
                            lifespan,
                        )

                    def refresh(admin_only: bool = registration.admin_only) -> CurrentUser:
                        current = self.actor(actor)
                        if admin_only and not current.has_platform_role("admin"):
                            raise ForbiddenError("MCP requires an administrator")
                        return current

                    server = AuthorizedMCP(
                        native,
                        invocation,
                        refresh,
                        binding.resource_id,
                        registration.authorize,
                        {"require_approval": "always"},
                    )
                    yield server, context.manifest.version, payload_digest(dict(config))
                return
        raise NotFoundError("The workflow MCP resource is not installed")

    async def mcp_descriptor(
        self, server: MCPServer, version: str, config_digest: str, operation: str
    ) -> dict[str, object]:
        for tool in await server.list_tools():
            if tool.name == operation:
                return {
                    "parameters": tool.input_schema,
                    "descriptor": tool.model_dump(mode="json"),
                    "plugin_version": version,
                    "config_digest": config_digest,
                    "requires_approval": True,
                }
        raise NotFoundError("The MCP server does not expose this operation")

    def allowed(self, binding: ToolBinding, operation: str) -> None:
        if operation not in binding.allowed_operations:
            raise ForbiddenError("The workflow binding does not allow this operation")

    async def describe(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        operation: str,
        *,
        source: InvocationSource | None = None,
    ) -> dict[str, object]:
        actor = self.actor(actor)
        self.allowed(binding, operation)
        if binding.kind == "plugin":
            installed = authorize_registered_tool(
                self.plugins(),
                binding.resource_id,
                operation,
                actor,
            )
            await authorize_tool_policy(
                installed,
                AuthorizationRequest(
                    Principal(user_id=actor.user_id, roles=actor.platform_roles),
                    "discover",
                    f"{binding.resource_id}/tools/{operation}",
                    source or InvocationSource(),
                ),
            )
            return self.plugin_descriptor(self.plugins(), actor, binding, operation)
        async with self.mcp(actor, binding, source or InvocationSource()) as (
            server,
            version,
            config_digest,
        ):
            return await self.mcp_descriptor(server, version, config_digest, operation)

    async def authorize(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        operation: str,
        arguments: dict[str, object],
        source: InvocationSource,
    ) -> None:
        actor = self.actor(actor)
        self.allowed(binding, operation)
        if binding.kind == "plugin":
            installed = authorize_registered_tool(
                self.plugins(),
                binding.resource_id,
                operation,
                actor,
            )
            await authorize_tool_policy(
                installed,
                AuthorizationRequest(
                    Principal(user_id=actor.user_id, roles=actor.platform_roles),
                    "execute",
                    f"{binding.resource_id}/tools/{operation}",
                    source,
                    arguments,
                ),
            )
            return
        async with self.mcp(actor, binding, source) as (server, _, _):
            assert isinstance(server, AuthorizedMCP)
            await server.authorize("execute", "tools/" + operation, arguments)

    async def invoke(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        operation: str,
        arguments: dict[str, object],
        operation_id: UUID,
        descriptor: dict[str, object],
        *,
        source: InvocationSource | None = None,
    ) -> object:
        actor = self.actor(actor)
        self.allowed(binding, operation)
        if binding.kind == "plugin":
            registry = self.plugins()
            if self.plugin_descriptor(registry, actor, binding, operation) != descriptor:
                raise ConflictError("The installed tool changed after approval")
            # Use this same installation snapshot for descriptor validation and invocation.
            return await invoke_registered_tool(
                registry,
                binding.resource_id,
                operation,
                arguments,
                actor,
                operation_id,
                binding.config,
                source=source,
            )
        async with self.mcp(actor, binding, source or InvocationSource()) as (
            server,
            version,
            config_digest,
        ):
            if await self.mcp_descriptor(server, version, config_digest, operation) != descriptor:
                raise ConflictError("The MCP tool changed after approval")
            # Use the same native connection that supplied the validated descriptor.
            result = await server.call_tool(operation, arguments)
            return result.model_dump(mode="json")


def configure_workflows(app: FastAPI) -> None:
    registry = EngineRegistry()
    registry.register("temporal", TemporalWorkflowEngine)
    app.state.workflow_engines = registry
