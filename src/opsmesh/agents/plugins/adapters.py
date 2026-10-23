"""Feature-owned preparation of Agent plugin resources; no connections at load time."""

from pathlib import Path
from typing import Any

from agents.function_schema import function_schema
from opsmesh_sdk import PluginContext
from opsmesh_sdk.hooks import HookRegistration
from opsmesh_sdk.registration import (
    ConfigField,
    MCPRegistration,
    MiddlewareRegistration,
    ToolRegistration,
    resolve_config,
)
from pydantic import TypeAdapter

from opsmesh.agents.plugins.registry import InstalledTool
from opsmesh.agents.skills.packages import build_skill_archive
from opsmesh.plugins.base import PluginAdapter, PreparedResource


def validate_defaults(
    context: PluginContext,
    section: str,
    name: str,
    fields: tuple[ConfigField, ...],
) -> None:
    defaults = context.config.get(section, {})
    if not isinstance(defaults, dict) or not isinstance(defaults.get(name, {}), dict):
        raise ValueError("Installed resource configuration must be an object")
    # Required values may be supplied by a later per-Agent binding.
    optional = tuple(item.model_copy(update={"required": False}) for item in fields)
    resolve_config(optional, defaults.get(name, {}))


def resource(
    context: PluginContext, kind: str, resource_name: str, value: object, **metadata: object
) -> PreparedResource:
    segment = {"tool": "tools", "skill": "skills"}.get(kind, kind)
    identity = f"{context.manifest.id}/{segment}"
    if kind not in {"hook", "middleware"}:
        identity += f"/{resource_name}"
    elif kind == "hook":
        identity = f"{context.manifest.id}/hooks"
    return PreparedResource(identity, context.manifest.id, kind, resource_name, value, metadata)


class ToolAdapter(PluginAdapter):
    kind = "tool"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        import inspect

        if not isinstance(registration, ToolRegistration):
            raise TypeError("Expected a tool registration")
        validate_defaults(context, "tools", name, registration.config_fields)
        schema = function_schema(
            registration.fn,
            name_override=name,
            description_override=registration.description or None,
            strict_json_schema=False,
        )
        if schema.takes_context:
            raise ValueError("Plugin functions use get_invocation(), not an SDK context argument")
        installed = InstalledTool(
            registration,
            schema,
            TypeAdapter(
                Any
                if schema.return_annotation is inspect.Signature.empty
                else schema.return_annotation
            ),
        )
        info = installed.describe(context.manifest.id)
        return resource(
            context,
            self.kind,
            name,
            installed,
            **info.model_dump(exclude={"id", "source"}),
            config_fields=[v.model_dump() for v in registration.config_fields],
        )


class SkillAdapter(PluginAdapter):
    kind = "skill"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, Path):
            raise TypeError("Expected a Skill directory")
        archive = build_skill_archive(registration)
        return resource(context, self.kind, name, archive)


class MCPAdapter(PluginAdapter):
    kind = "mcp"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, MCPRegistration) or not callable(registration.factory):
            raise TypeError("Expected an MCP factory registration")
        validate_defaults(context, "mcp", name, registration.config_fields)
        return resource(
            context,
            self.kind,
            name,
            registration,
            description=registration.description,
            admin_only=registration.admin_only,
            config_fields=[v.model_dump() for v in registration.config_fields],
        )


class HookAdapter(PluginAdapter):
    kind = "hook"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, tuple) or not all(
            isinstance(v, HookRegistration) for v in registration
        ):
            raise TypeError("Expected lifecycle hook registrations")
        return resource(context, self.kind, name, registration)


class MiddlewareAdapter(PluginAdapter):
    kind = "middleware"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, tuple) or not all(
            isinstance(v, MiddlewareRegistration) for v in registration
        ):
            raise TypeError("Expected model middleware registrations")
        return resource(context, self.kind, name, registration)
