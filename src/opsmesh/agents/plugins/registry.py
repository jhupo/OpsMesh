"""Installed plugin registry and native Agents SDK function adaptation."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agents.function_schema import FuncSchema, function_schema
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from opsmesh_sdk import PluginContext
from opsmesh_sdk.channel_management import ManagementAccess
from opsmesh_sdk.identity import AuthorizationRequest, InvocationSource, Principal
from opsmesh_sdk.registration import (
    InvocationContext,
    MiddlewareRegistration,
    ToolRegistration,
    resolve_config,
    tool_invocation,
)
from pydantic import SecretStr, TypeAdapter, ValidationError

from opsmesh.agents.tools import ToolInfo
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.network import CredentialResolver
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser, ensure_active_user

if TYPE_CHECKING:
    from collections.abc import Collection

    from opsmesh.agents import AssemblyBindings
    from opsmesh.agents.plugins.bindings import PluginToolInvoker, PluginToolVisibility
    from opsmesh.agents.plugins.mcp import PluginMCPConnector


@dataclass(frozen=True)
class InstalledTool:
    registration: ToolRegistration
    schema: FuncSchema
    result: TypeAdapter[Any]

    def describe(self, plugin_id: str) -> ToolInfo:
        item = self.registration
        model_name = f"{plugin_id.replace('-', '_')}__{item.name}"
        if len(model_name) > 64:
            model_name = f"{model_name[:47]}_{sha256(model_name.encode()).hexdigest()[:16]}"
        parameters = self.schema.params_json_schema
        output_schema = self.result.json_schema()
        Draft202012Validator.check_schema(parameters)
        Draft202012Validator.check_schema(output_schema)
        return ToolInfo(
            id=f"{plugin_id}/tools/{item.name}",
            name=model_name,
            description=self.schema.description or "",
            source=f"plugin:{plugin_id}",
            parameters=parameters,
            output_schema=output_schema,
            timeout_seconds=item.timeout_seconds,
            effects=list(item.effects),
            requires_approval=item.requires_approval,
            admin_only=item.admin_only,
        )


@dataclass
class PluginRegistry:
    plugins: dict[str, PluginContext] = field(default_factory=dict)
    tools: dict[tuple[str, str], InstalledTool] = field(default_factory=dict)

    def describe_tool(self, plugin_id: str, name: str) -> ToolInfo:
        plugin = self.plugins[plugin_id]
        return (
            self.tools[plugin_id, name]
            .describe(plugin_id)
            .model_copy(
                update={
                    "revision": payload_digest(
                        {"version": plugin.manifest.version, "config": dict(plugin.config)}
                    )
                }
            )
        )

    def register(
        self,
        context: PluginContext,
        *,
        prepared_tools: dict[tuple[str, str], InstalledTool] | None = None,
    ) -> None:
        plugin_id = context.manifest.id
        if plugin_id in self.plugins:
            raise ValueError("Duplicate plugin ID")
        if prepared_tools is not None:
            self.tools.update(
                {(plugin_id, name): prepared_tools[plugin_id, name] for name in context.tools}
            )
            self.plugins[plugin_id] = context
            return
        compiled = {}
        for name, registration in context.tools.items():
            schema = function_schema(
                registration.fn,
                name_override=name,
                description_override=registration.description or None,
                strict_json_schema=False,
            )
            if schema.takes_context:
                raise ValueError(
                    "Plugin functions use get_invocation(), not an SDK context argument"
                )
            result: TypeAdapter[Any] = TypeAdapter(
                Any
                if schema.return_annotation is inspect.Signature.empty
                else schema.return_annotation
            )
            tool = InstalledTool(registration, schema, result)
            tool.describe(plugin_id)  # Validate both schemas before accepting the plugin.
            compiled[plugin_id, name] = tool
        self.tools.update(compiled)
        self.plugins[plugin_id] = context

    def middleware(self) -> list[MiddlewareRegistration]:
        return sorted(
            [item for context in self.plugins.values() for item in context.middlewares],
            key=lambda item: item.priority,
        )

    def agent_bindings(
        self,
        *,
        invoke_tool: PluginToolInvoker,
        visible: PluginToolVisibility | None = None,
        plugin_ids: Collection[str] | None = None,
        include_admin_mcp: bool = False,
        connect_mcp: PluginMCPConnector | None = None,
    ) -> AssemblyBindings:
        from opsmesh.agents import AssemblyBindings
        from opsmesh.agents.plugins.bindings import register_agent_plugins
        from opsmesh.agents.plugins.mcp import connect_plugin_mcp

        bindings = AssemblyBindings()
        register_agent_plugins(
            self,
            bindings,
            invoke_tool=invoke_tool,
            visible=visible,
            plugin_ids=plugin_ids,
            include_admin_mcp=include_admin_mcp,
            connect_mcp=connect_mcp or connect_plugin_mcp,
        )
        return bindings


def authorize_registered_tool(
    registry: PluginRegistry,
    plugin_id: str,
    name: str,
    user: CurrentUser,
    *,
    expected_version: str | None = None,
) -> InstalledTool:
    ensure_active_user(user)
    context = registry.plugins.get(plugin_id)
    installed = registry.tools.get((plugin_id, name))
    if context is None or installed is None:
        raise NotFoundError("Plugin tool not found")
    if expected_version is not None and context.manifest.version != expected_version:
        raise ConflictError("The requested plugin version is not loaded by this process")
    if installed.registration.admin_only and not user.has_platform_role("admin"):
        raise ForbiddenError("This system plugin tool requires an administrator")
    return installed


async def invoke_registered_tool(
    registry: PluginRegistry,
    plugin_id: str,
    name: str,
    arguments: dict[str, object],
    user: CurrentUser,
    operation_id: UUID,
    config: dict[str, object] | None = None,
    source: InvocationSource | None = None,
    credentials: CredentialResolver | None = None,
    management: ManagementAccess | None = None,
) -> object:
    installed = authorize_registered_tool(registry, plugin_id, name, user)
    context = registry.plugins[plugin_id]
    tool = installed.registration
    admin = user.has_platform_role("admin")
    try:
        values = installed.schema.params_pydantic_model.model_validate(arguments)
        # Pydantic functions allow no implicit expansion of undeclared model arguments.
        Draft202012Validator(installed.schema.params_json_schema).validate(arguments)
        defaults = context.config.get("tools", {})
        if not isinstance(defaults, dict) or not isinstance(defaults.get(name, {}), dict):
            raise ValueError("Installed tool configuration must be an object")
        configuration = resolve_config(
            tool.config_fields, {**defaults.get(name, {}), **(config or {})}
        )
    except (ValidationError, SchemaValidationError, ValueError) as exc:
        raise InvalidArgumentError("Invalid plugin arguments or configuration") from exc

    principal = Principal(user_id=user.user_id, roles=user.platform_roles)
    origin = source or InvocationSource()
    await authorize_tool_policy(
        installed,
        AuthorizationRequest(principal, "execute", f"{plugin_id}/tools/{name}", origin, arguments),
    )
    args, kwargs = installed.schema.to_call_args(values)
    secrets: dict[str, SecretStr] = {}
    for alias, reference in context.credential_refs.items():
        if credentials is None:
            raise InvalidArgumentError("The plugin credential resolver is unavailable")
        secret = await credentials(reference)
        if not isinstance(secret, SecretStr) or not secret.get_secret_value():
            raise InvalidArgumentError("A declared plugin credential is unavailable")
        secrets[alias] = secret
    try:
        with tool_invocation(
            InvocationContext(
                operation_id,
                user.user_id,
                admin,
                configuration,
                name,
                principal,
                origin,
                secrets,
                management,
            )
        ):
            # Await the sync function's thread even on timeout/cancellation: a running
            # thread cannot be cancelled, and its configuration cannot outlive its call.
            async def invoke() -> Any:
                if inspect.iscoroutinefunction(tool.fn):
                    return await tool.fn(*args, **kwargs)
                task = asyncio.create_task(asyncio.to_thread(tool.fn, *args, **kwargs))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise

            async with asyncio.timeout(tool.timeout_seconds):
                value = await invoke()
            value = installed.result.dump_python(
                installed.result.validate_python(value), mode="json"
            )
            if len(json.dumps(value, allow_nan=False).encode()) > 1_000_000:
                raise InvalidArgumentError("Plugin tool output exceeds the size limit")
            return value
    except (ValidationError, SchemaValidationError) as exc:
        raise InvalidArgumentError("Plugin result does not match its return annotation") from exc


async def authorize_tool_policy(installed: InstalledTool, request: AuthorizationRequest) -> None:
    """Business authorization stays in the plugin; exceptions fail closed."""
    policy = installed.registration.authorize
    if policy is None:
        return
    result = policy(request)
    if inspect.isawaitable(result):
        result = await result
    if result is not True:
        raise ForbiddenError("The tool policy denied this operation")
