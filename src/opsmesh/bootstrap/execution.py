"""Compose execution with application-owned configuration and credential catalogs."""

import inspect
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from agents.mcp import MCPServer
from fastapi import FastAPI
from opsmesh_sdk import PluginContext
from opsmesh_sdk.identity import AuthorizationRequest, Principal
from opsmesh_sdk.registration import (
    InvocationContext,
    MCPRegistration,
    resolve_config,
    tool_invocation,
)
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.assembly import AssemblyBindings
from opsmesh.agents.plugins.mcp import connect_plugin_mcp
from opsmesh.agents.plugins.registry import PluginRegistry
from opsmesh.agents.providers.factory import CredentialResolver
from opsmesh.agents.providers.registry import ProviderRegistry
from opsmesh.execution.context import ExecutionContext
from opsmesh.execution.contracts import PreparedAgent
from opsmesh.execution.instances.access import current_user
from opsmesh.execution.mcp import AuthorizedMCP
from opsmesh.execution.preparation import DefinitionLoader
from opsmesh.execution.runs.executor import BindingsFactory, RunExecutor
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.execution.tools import PluginTools
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.engines.temporal.execution_dispatch import (
    dispatch_cancellation,
    dispatch_run,
    dispatch_sandbox,
)


def native_bindings_factory(
    sessions: sessionmaker[Session],
    cipher: SecretCipher,
    *,
    plugins: Callable[[], PluginRegistry],
    providers: Callable[[], ProviderRegistry],
    credentials: CredentialResolver | None,
) -> BindingsFactory:
    def bindings(context: ExecutionContext, prepared: PreparedAgent) -> AssemblyBindings:
        ledger = OperationLedger(sessions, cipher, context)
        with sessions.begin() as session:
            actor = current_user(session, context.user_id)
        registry = plugins()
        tools = PluginTools(ledger, plugins, registry)

        async def connect_mcp(
            plugin: PluginContext,
            registration: MCPRegistration,
            config: Mapping[str, object],
            options: dict[str, Any],
            lifespan: AsyncExitStack,
        ) -> MCPServer:
            def actor_now() -> CurrentUser:
                ledger.authorize()
                with sessions() as session:
                    current = current_user(session, context.user_id)
                    if registration.admin_only and not current.has_platform_role("admin"):
                        raise ForbiddenError("MCP requires an administrator")
                    return current

            current = actor_now()
            resource = f"{plugin.manifest.id}/mcp/{registration.name}"
            defaults = plugin.config.get("mcp", {})
            if not isinstance(defaults, dict) or not isinstance(
                defaults.get(registration.name, {}), dict
            ):
                raise ValueError("Installed MCP configuration must be an object")
            values = resolve_config(
                registration.config_fields,
                {**defaults.get(registration.name, {}), **config},
            )
            if registration.authorize is not None:
                allowed = registration.authorize(
                    AuthorizationRequest(
                        Principal(user_id=current.user_id, roles=current.platform_roles),
                        "execute",
                        resource + "/connect",
                        context.source,
                    )
                )
                if inspect.isawaitable(allowed):
                    allowed = await allowed
                if allowed is not True:
                    raise ForbiddenError("MCP connection policy denied this caller")
            invocation = InvocationContext(
                uuid5(NAMESPACE_URL, f"{context.run_id}:{resource}"),
                current.user_id,
                current.has_platform_role("admin"),
                values,
                resource,
                Principal(user_id=current.user_id, roles=current.platform_roles),
                context.source,
            )
            with tool_invocation(invocation):
                native = await connect_plugin_mcp(plugin, registration, config, options, lifespan)
            return AuthorizedMCP(
                native, invocation, actor_now, resource, registration.authorize, options
            )

        result = registry.agent_bindings(
            invoke_tool=tools.invoke,
            visible=tools.visible,
            include_admin_mcp=actor.has_platform_role("admin"),
            connect_mcp=connect_mcp,
        )
        admitted = ProviderRegistry()
        if prepared.provider_configs is not None:
            for config in prepared.provider_configs.values():
                admitted.register(config)
        else:
            admitted = providers()
        result.providers, result.credentials = admitted, credentials
        return result

    return bindings


def configure_execution(
    app: FastAPI,
    *,
    sessions: sessionmaker[Session],
    cipher: SecretCipher,
    definitions: Callable[[Session, CurrentUser], DefinitionLoader],
    bindings: BindingsFactory,
    history_root: Path,
) -> RunExecutor:
    """The host may substitute durable/authorized definition and resource loaders."""
    app.state.execution_definition_loader = definitions
    executor = RunExecutor(sessions, cipher, bindings=bindings, history_root=history_root)
    app.state.execution_executor = executor
    app.state.execution_run_dispatchers = {"temporal": dispatch_run}
    app.state.execution_cancel_dispatcher = dispatch_cancellation
    app.state.execution_operation_dispatcher = dispatch_sandbox
    return executor
