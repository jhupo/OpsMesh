"""Authorized plugin calls with durable effect identities."""

from collections.abc import Callable, Mapping
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from agents.tool_context import ToolContext
from opsmesh_sdk.identity import AuthorizationRequest, Principal

from opsmesh.agents.plugins.registry import (
    PluginRegistry,
    authorize_registered_tool,
    authorize_tool_policy,
    invoke_registered_tool,
)
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.network import CredentialResolver
from opsmesh.users.service import current_user


class PluginTools:
    def __init__(
        self,
        ledger: OperationLedger,
        registry: Callable[[], PluginRegistry],
        admitted: PluginRegistry,
        credentials: CredentialResolver | None = None,
    ) -> None:
        self.ledger, self.registry, self.admitted = ledger, registry, admitted
        self.credentials = credentials

    def current(self, plugin_id: str, name: str) -> PluginRegistry:
        registry = self.registry()
        if (plugin_id, name) not in registry.tools or registry.describe_tool(
            plugin_id, name
        ) != self.admitted.describe_tool(plugin_id, name):
            raise ConflictError("The plugin tool changed after admission")
        return registry

    async def invoke(
        self,
        plugin_id: str,
        name: str,
        context: ToolContext[Any],
        arguments: dict[str, Any],
        config: Mapping[str, object],
    ) -> object:
        registry = self.current(plugin_id, name)
        with self.ledger.sessions.begin() as session:
            actor = current_user(session, self.ledger.context.user_id)
            installed = authorize_registered_tool(registry, plugin_id, name, actor)
        await authorize_tool_policy(
            installed,
            AuthorizationRequest(
                Principal(user_id=actor.user_id, roles=actor.platform_roles),
                "execute",
                f"{plugin_id}/tools/{name}",
                self.ledger.context.source,
                arguments,
            ),
        )
        operation_id = self.ledger.context.operation_id("tool", context.tool_call_id)
        cached = self.ledger.claim(
            operation_id,
            "tool",
            {
                "plugin_id": plugin_id,
                "version": registry.plugins[plugin_id].manifest.version,
                "name": name,
                "arguments": arguments,
                "config": dict(config),
            },
        )
        if cached is not None:
            return cached["output"]
        try:
            result = await invoke_registered_tool(
                registry,
                plugin_id,
                name,
                arguments,
                actor,
                operation_id=uuid5(NAMESPACE_URL, operation_id),
                config=dict(config),
                source=self.ledger.context.source,
                credentials=self.credentials,
            )
            self.ledger.complete(operation_id, {"output": result})
            return result
        except BaseException:
            self.ledger.uncertain(operation_id)
            raise

    async def visible(self, plugin_id: str, name: str) -> bool:
        from opsmesh.shared.errors import ForbiddenError

        try:
            self.ledger.authorize()
            with self.ledger.sessions() as session:
                actor = current_user(session, self.ledger.context.user_id)
                installed = authorize_registered_tool(
                    self.current(plugin_id, name), plugin_id, name, actor
                )
            await authorize_tool_policy(
                installed,
                AuthorizationRequest(
                    Principal(user_id=actor.user_id, roles=actor.platform_roles),
                    "discover",
                    f"{plugin_id}/tools/{name}",
                    self.ledger.context.source,
                ),
            )
            return True
        except ForbiddenError:
            return False
