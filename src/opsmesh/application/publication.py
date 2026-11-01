"""Code declarations use the same authorized resource services as HTTP management."""

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.conversations import ConversationInbox, ConversationInboxProvider
from opsmesh.modules.capabilities.api import CodeToolRegistry, ToolCallback, ToolDefinition

from .conversations import ConversationApplication, ConversationGateway, ConversationPolicy
from .conversations.configuration import ConversationOptions
from .conversations.reconfiguration import reconfiguration_scope
from .deployment import (
    ApplicationDefinition,
    DeploymentResult,
    DeploymentServices,
    OpsMeshApplicationDeployment,
)


class ApplicationPublication:
    """Own declarations and gateway preparation, without a second configuration store."""

    def __init__(
        self,
        services: DeploymentServices,
        inboxes: ConversationInboxProvider,
        data_directory: Path,
        conversations: Callable[
            [ConversationOptions, ConversationPolicy, ConversationInbox], ConversationApplication
        ],
    ) -> None:
        self.services, self.inboxes, self.data_directory = services, inboxes, data_directory
        self._conversations = conversations
        self._definition = ApplicationDefinition()
        self._tools: dict[str, ToolDefinition] = {}
        self._gateways: dict[str, tuple[ConversationGateway, ConversationPolicy]] = {}
        self._prepared = False

    def define(self, definition: ApplicationDefinition) -> None:
        if self._prepared:
            raise OpsMeshError("CONFLICT", "Application declarations are frozen after startup")
        self._merge_tools(definition.tools)
        self._definition = definition

    def _merge_tools(self, declarations: tuple[ToolDefinition, ...]) -> tuple[ToolDefinition, ...]:
        if len({tool.ref for tool in declarations}) != len(declarations):
            raise OpsMeshError("CONFIG_INVALID", "Duplicate tool in application definition")
        merged = dict(self._tools)
        for tool in declarations:
            if tool.ref in merged and merged[tool.ref] != tool:
                raise OpsMeshError("CONFLICT", "Tool registration differs from its declaration")
            merged[tool.ref] = tool
        return tuple(merged.values())

    @property
    def definition(self) -> ApplicationDefinition:
        return replace(self._definition, tools=self._merge_tools(self._definition.tools))

    def register_tool(
        self, definition: ToolDefinition, callback: ToolCallback, registry: CodeToolRegistry
    ) -> None:
        if self._prepared or definition.ref in self._tools:
            raise OpsMeshError("CONFLICT", "Code tool is frozen or already registered")
        declared = next(
            (item for item in self._definition.tools if item.ref == definition.ref), None
        )
        if declared is not None and declared != definition:
            raise OpsMeshError("CONFLICT", "Tool registration differs from its declaration")
        registry.register(definition.code_ref, callback)
        self._tools[definition.ref] = definition

    def add_gateway(self, gateway: ConversationGateway, policy: ConversationPolicy) -> None:
        if self._prepared or gateway.scope in self._gateways:
            raise OpsMeshError("CONFLICT", "Conversation gateway is frozen or already declared")
        self._gateways[gateway.scope] = gateway, policy

    async def publish(self, *, actor_id: str) -> DeploymentResult:
        if self._prepared:
            raise OpsMeshError("CONFLICT", "Stop message consumers before publishing resources")
        async with AsyncExitStack() as ownership:
            for scope in sorted(self._gateways):
                await ownership.enter_async_context(
                    reconfiguration_scope(
                        self.data_directory / "conversations" / scope,
                        self.inboxes.open(scope),
                    )
                )
            return await OpsMeshApplicationDeployment(self.services).apply(
                self.definition, actor_id=actor_id
            )

    async def prepare(self) -> None:
        if self._prepared:
            return
        # Resolve every route first so an invalid declaration cannot register half a pipeline.
        applications = []
        for scope, (gateway, policy) in self._gateways.items():
            options = await gateway.resolve(
                self.services.bots, self.services.agents, self.data_directory
            )
            applications.append(self._conversations(options, policy, self.inboxes.open(scope)))
        for application in applications:
            application.register()
        self._prepared = True
