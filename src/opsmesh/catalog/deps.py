import inspect
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents import FunctionTool, RunContextWrapper
from agents.tool_context import ToolContext
from opsmesh_sdk import InvocationContext
from sqlalchemy.orm import sessionmaker

from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import load
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.service import identity

from .models import CatalogPublication
from .service import authorize_pins, get


class InvocationDependencies:
    def __init__(
        self,
        sessions: sessionmaker,
        cipher: SecretCipher,
        registry: PluginRegistry,
        root: Path,
        pins: dict[str, Any],
        caller_id: str,
        invoke: Callable[[CatalogPublication, Any, str], Awaitable[Any]],
    ):
        self.sessions, self.cipher, self.registry, self.root = sessions, cipher, registry, root
        self.pins, self.caller_id, self.invoke = pins, caller_id, invoke
        self.context: InvocationContext | None = None

    def publication(self, alias: str, kind: str) -> CatalogPublication:
        pin = self.pins.get(alias)
        if pin is None or pin["kind"] != kind:
            raise NotFoundError(f"Dependency not declared: {alias}")
        with self.sessions.begin() as db:
            user = identity(db, self.caller_id)
            row = get(db, user, pin["id"])
            if row.version != pin["version"]:
                raise ConflictError("Dependency version mismatch")
            authorize_pins(db, user, row.dependencies)
            return row

    def _context(self, row: CatalogPublication) -> InvocationContext:
        assert self.context is not None
        with self.sessions.begin() as db:
            user = identity(db, self.caller_id)
        # A contribution's own dependencies are isolated from the caller's aliases.
        nested = InvocationDependencies(
            self.sessions,
            self.cipher,
            self.registry,
            self.root,
            row.dependencies,
            self.caller_id,
            self.invoke,
        )
        context = replace(
            self.context,
            user=user,
            config=self.cipher.decrypt(row.encrypted_config),
            dependencies=nested,
        )
        nested.context = context
        return context

    def _plugin(self, row: CatalogPublication) -> Any:
        with self.sessions.begin() as db:
            return load(db, self.root, self.registry, row.plugin_id)

    async def tool(self, alias: str) -> Any:
        row = self.publication(alias, "tool")
        registered = self._plugin(row).tools[row.contribution]
        if not isinstance(registered, FunctionTool):
            raise TypeError("Tool contributions must register a native FunctionTool")
        native = replace(registered, name=alias)
        original = native.on_invoke_tool

        async def invoke(context: ToolContext[Any], arguments: str) -> Any:
            current = self.publication(alias, "tool")
            tool_context = ToolContext.from_agent_context(
                context,
                tool_call_id=context.tool_call_id,
                tool_name=context.tool_name,
                tool_arguments=arguments,
                tool_call=context.tool_call,
                tool_namespace=context.tool_namespace,
            )
            tool_context.context = self._context(current)
            return await original(tool_context, arguments)

        native.on_invoke_tool = invoke
        return native

    @asynccontextmanager
    async def mcp(self, alias: str) -> Any:
        row = self.publication(alias, "mcp")
        server = self._plugin(row).mcps[row.contribution](self._context(row))
        if inspect.isawaitable(server):
            server = await server
        original = server.call_tool
        original_resolver = server.tool_meta_resolver

        async def resolve_metadata(data: Any) -> Any:
            current = self.publication(alias, "mcp")
            fresh = RunContextWrapper(
                context=self._context(current),
                usage=data.run_context.usage,
                turn_input=data.run_context.turn_input,
                tool_input=data.run_context.tool_input,
            )
            result = original_resolver(replace(data, run_context=fresh))
            return await result if inspect.isawaitable(result) else result

        async def checked_call(*args: Any, **kwargs: Any) -> Any:
            self.publication(alias, "mcp")
            return await original(*args, **kwargs)

        server.call_tool = checked_call
        if original_resolver is not None:
            server.tool_meta_resolver = resolve_metadata
        # The SDK owns connection setup, tool discovery and cleanup.
        try:
            async with server:
                yield server
        finally:
            server.call_tool = original
            server.tool_meta_resolver = original_resolver

    async def skill(self, alias: str) -> Path:
        row = self.publication(alias, "skill")
        return self._plugin(row).skills[row.contribution]

    async def call(self, alias: str, value: Any, *, key: str) -> Any:
        if not key or len(key) > 100:
            raise ValueError("A stable child invocation key is required")
        return await self.invoke(self.publication(alias, "agent"), value, key)
