"""Authorization around native MCP RPCs; transport and protocol remain SDK-owned."""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from uuid import uuid5

from agents import RunContextWrapper
from agents.agent import AgentBase
from agents.mcp import MCPServer
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ReadResourceResult,
    Tool,
)
from opsmesh_sdk.identity import AuthorizationRequest, Principal, ToolAuthorizer
from opsmesh_sdk.registration import InvocationContext, tool_invocation

from opsmesh.shared.errors import ForbiddenError
from opsmesh.users.access import CurrentUser


class AuthorizedMCP(MCPServer):
    def __init__(
        self,
        native: MCPServer,
        context: InvocationContext,
        actor: Callable[[], CurrentUser],
        resource: str,
        policy: ToolAuthorizer | None,
        options: dict[str, Any],
    ) -> None:
        super().__init__(
            use_structured_content=native.use_structured_content,
            require_approval=options.get("require_approval", "always"),
            tool_meta_resolver=native.tool_meta_resolver,
            custom_data_extractor=native.custom_data_extractor,
            tool_input_guardrails=native.tool_input_guardrails,
            tool_output_guardrails=native.tool_output_guardrails,
        )
        self.native, self.context, self.actor = native, context, actor
        self.resource, self.policy = resource, policy
        self.filter = options.get("tool_filter", {})

    @property
    def name(self) -> str:
        return self.native.name

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def authorize(self, action: str, suffix: str, arguments: dict[str, Any]) -> CurrentUser:
        actor = self.actor()
        if self.policy is not None:
            request = AuthorizationRequest(
                Principal(user_id=actor.user_id, roles=actor.platform_roles),
                "discover" if action == "discover" else "execute",
                self.resource + "/" + suffix,
                self.context.source,
                arguments,
            )
            allowed = self.policy(request)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if allowed is not True:
                raise ForbiddenError("MCP resource policy denied this operation")
        return actor

    @contextmanager
    def invocation(self, actor: CurrentUser, name: str) -> Iterator[None]:
        with tool_invocation(
            replace(
                self.context,
                operation_id=uuid5(self.context.operation_id, name),
                user_id=actor.user_id,
                is_admin=actor.has_platform_role("admin"),
                principal=Principal(user_id=actor.user_id, roles=actor.platform_roles),
            )
        ):
            yield

    def selected(self, name: str) -> bool:
        if not isinstance(self.filter, dict):
            return True
        allowed = self.filter.get("allowed_tool_names")
        return (allowed is None or name in allowed) and name not in self.filter.get(
            "blocked_tool_names", []
        )

    async def list_tools(
        self, run_context: RunContextWrapper[Any] | None = None, agent: AgentBase[Any] | None = None
    ) -> list[Tool]:
        actor = await self.authorize("discover", "tools", {})
        with self.invocation(actor, "list_tools"):
            tools = await self.native.list_tools(run_context, agent)
        result = []
        for tool in tools:
            if not self.selected(tool.name):
                continue
            try:
                await self.authorize("discover", "tools/" + tool.name, {})
            except ForbiddenError:
                continue
            result.append(tool)
        return result

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        if not self.selected(tool_name):
            raise ForbiddenError("MCP tool is outside the Agent binding")
        actor = await self.authorize("execute", "tools/" + tool_name, arguments or {})
        with self.invocation(actor, tool_name):
            return await self.native.call_tool(tool_name, arguments, meta)

    async def list_prompts(self) -> ListPromptsResult:
        actor = await self.authorize("discover", "prompts", {})
        with self.invocation(actor, "list_prompts"):
            return await self.native.list_prompts()

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        actor = await self.authorize("execute", "prompts/" + name, arguments or {})
        with self.invocation(actor, "get_prompt"):
            return await self.native.get_prompt(name, arguments)

    async def list_resources(self, cursor: str | None = None) -> ListResourcesResult:
        actor = await self.authorize("discover", "resources", {})
        with self.invocation(actor, "list_resources"):
            return await self.native.list_resources(cursor)

    async def list_resource_templates(
        self, cursor: str | None = None
    ) -> ListResourceTemplatesResult:
        actor = await self.authorize("discover", "resource_templates", {})
        with self.invocation(actor, "list_resource_templates"):
            return await self.native.list_resource_templates(cursor)

    async def read_resource(self, uri: str) -> ReadResourceResult:
        actor = await self.authorize("execute", "resources/read", {"uri": uri})
        with self.invocation(actor, "read_resource"):
            return await self.native.read_resource(uri)
