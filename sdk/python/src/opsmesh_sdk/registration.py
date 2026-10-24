"""Plugin registration, independent of the server and execution engine."""

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from opsmesh_sdk.channel_events import EventHandler, EventHandlerRegistration
from opsmesh_sdk.channel_management import ManagementAccess
from opsmesh_sdk.channels import ChannelAdapter
from opsmesh_sdk.hooks import HOOK_POINTS, HookHandler, HookPoint, HookRegistration
from opsmesh_sdk.identity import InvocationSource, Principal, ToolAuthorizer
from opsmesh_sdk.manifest import PluginManifest
from opsmesh_sdk.messages import MessageFilter, MessageFilterRegistration
from opsmesh_sdk.middleware import ModelMiddleware


class ConfigField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    type: Literal["text", "password", "number", "boolean", "select"] = "text"
    label: str = ""
    description: str = ""
    required: bool = False
    default: str | int | float | bool | None = None
    options: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class InvocationContext:
    operation_id: UUID
    user_id: UUID
    is_admin: bool
    config: Mapping[str, object] = field(repr=False)
    tool_name: str = ""
    principal: Principal | None = None
    source: InvocationSource = field(default_factory=InvocationSource)
    credentials: Mapping[str, SecretStr] = field(default_factory=dict, repr=False)
    management: ManagementAccess | None = field(default=None, repr=False)


_invocation: ContextVar[InvocationContext] = ContextVar("opsmesh_plugin_invocation")


def get_tool_config(name: str | None = None) -> Mapping[str, object]:
    """Current binding's configuration, isolated between concurrent invocations."""
    context = _invocation.get()
    if name is not None and name != context.tool_name:
        raise ValueError("Configuration is scoped to the current tool")
    return context.config


def get_invocation() -> InvocationContext:
    return _invocation.get()


def get_plugin_credential(name: str) -> SecretStr:
    """Read a host-resolved credential declared by this installed plugin."""
    return _invocation.get().credentials[name]


@contextmanager
def tool_invocation(context: InvocationContext) -> Iterator[None]:
    token = _invocation.set(context)
    try:
        yield
    finally:
        _invocation.reset(token)


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    fn: Callable[..., Any]
    description: str
    config_fields: tuple[ConfigField, ...]
    admin_only: bool
    timeout_seconds: int
    effects: tuple[str, ...]
    requires_approval: bool
    authorize: ToolAuthorizer | None = None


@dataclass(frozen=True)
class MiddlewareRegistration:
    instance: ModelMiddleware
    priority: int = 100


@dataclass(frozen=True)
class ChannelRegistration:
    kind: str
    channel_cls: type[ChannelAdapter]
    label: str
    icon: str
    intro_url: str
    fields: tuple[ConfigField, ...]


@dataclass(frozen=True)
class MCPRegistration:
    name: str
    factory: Callable[..., Any]
    description: str
    config_fields: tuple[ConfigField, ...]
    admin_only: bool = False
    authorize: ToolAuthorizer | None = None


@dataclass
class PluginContext:
    manifest: PluginManifest
    source_path: Path
    data_directory: Path
    config: Mapping[str, object] = field(default_factory=dict)
    tools: dict[str, ToolRegistration] = field(default_factory=dict, init=False)
    middlewares: list[MiddlewareRegistration] = field(default_factory=list, init=False)
    hooks: list[HookRegistration] = field(default_factory=list, init=False)
    channels: dict[str, ChannelRegistration] = field(default_factory=dict, init=False)
    mcps: dict[str, MCPRegistration] = field(default_factory=dict, init=False)
    message_filters: dict[str, MessageFilterRegistration] = field(default_factory=dict, init=False)
    event_handlers: dict[str, EventHandlerRegistration] = field(default_factory=dict, init=False)
    skills_directory: Path | None = field(default=None, init=False)
    credential_refs: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.source_path = self.source_path.resolve(strict=True)
        self.config = MappingProxyType(dict(self.config))

    def credential(self, name: str, reference: str) -> None:
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)
            or name in self.credential_refs
            or not re.fullmatch(r"(?:env:[A-Z_][A-Z0-9_]*|secret:[A-Za-z0-9_.-]{1,190})", reference)
        ):
            raise ValueError("Plugin credentials require unique names and credential references")
        self.credential_refs[name] = reference

    def tool(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        description: str = "",
        config_fields: list[dict[str, Any] | ConfigField] | None = None,
        admin_only: bool = False,
        timeout_seconds: int = 60,
        effects: tuple[str, ...] = (),
        requires_approval: bool = False,
        authorize: ToolAuthorizer | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in self.tools:
            raise ValueError("Tool names must be unique public identifiers")
        if not callable(fn) or not 1 <= timeout_seconds <= 300:
            raise ValueError("A callable and a timeout between 1 and 300 seconds are required")
        if authorize is not None and not callable(authorize):
            raise TypeError("Tool authorization must be callable")
        if len(set(effects)) != len(effects) or not set(effects) <= {
            "model",
            "file_read",
            "file_write",
            "shell",
            "network",
            "database",
            "external_api",
        }:
            raise ValueError("Unknown or duplicate tool effects")
        self.tools[name] = ToolRegistration(
            name,
            fn,
            description,
            _fields(config_fields),
            admin_only,
            timeout_seconds,
            effects,
            requires_approval,
            authorize,
        )

    def skills(self, directory: str) -> None:
        relative = PurePosixPath(directory)
        path = self.source_path.joinpath(*relative.parts)
        if (
            self.skills_directory is not None
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in directory
            or ":" in directory
            or not path.resolve(strict=True).is_relative_to(self.source_path)
            or path.is_symlink()
            or not path.is_dir()
        ):
            raise ValueError("Register one skill directory inside the plugin")
        children = list(path.iterdir())
        if not children or any(
            not child.is_dir() or child.is_symlink() or not (child / "SKILL.md").is_file()
            for child in children
        ):
            raise ValueError("Each skill subdirectory must contain SKILL.md")
        self.skills_directory = path

    def middleware(self, instance: ModelMiddleware, *, priority: int = 100) -> None:
        if not isinstance(instance, ModelMiddleware):
            raise TypeError("Register a ModelMiddleware instance")
        self.middlewares.append(MiddlewareRegistration(instance, priority))

    def message_filter(
        self, name: str, handler: MessageFilter, *, timeout_seconds: float = 15
    ) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in self.message_filters:
            raise ValueError("Message filter names must be unique public identifiers")
        if not callable(handler) or not (
            iscoroutinefunction(handler) or iscoroutinefunction(type(handler).__call__)
        ):
            raise TypeError("Message filters must be async functions")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Message filter timeout must be between zero and 60 seconds")
        self.message_filters[name] = MessageFilterRegistration(handler, timeout_seconds)

    def conversation_event(
        self, name: str, handler: EventHandler, *, timeout_seconds: float = 10
    ) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in self.event_handlers:
            raise ValueError("Event handler names must be unique public identifiers")
        if not iscoroutinefunction(handler) or not 0 < timeout_seconds <= 60:
            raise ValueError("Event handlers require an async function and bounded timeout")
        self.event_handlers[name] = EventHandlerRegistration(handler, timeout_seconds)

    def mcp(
        self,
        name: str,
        factory: Callable[..., Any],
        *,
        description: str = "",
        config_fields: list[dict[str, Any] | ConfigField] | None = None,
        admin_only: bool = False,
        authorize: ToolAuthorizer | None = None,
    ) -> None:
        """Register factory(config, **native_options); the host owns its connection."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in self.mcps:
            raise ValueError("MCP names must be unique public identifiers")
        if not callable(factory):
            raise TypeError("MCP registration requires a factory")
        if authorize is not None and not callable(authorize):
            raise TypeError("MCP authorization must be callable")
        self.mcps[name] = MCPRegistration(
            name, factory, description, _fields(config_fields), admin_only, authorize
        )

    def contributions(self) -> dict[str, dict[str, object]]:
        """Explicit resource declarations; inspecting these never connects to a service."""
        return {
            "tool": dict(self.tools),
            "skill": {p.name: p for p in self.skills_directory.iterdir()}
            if self.skills_directory
            else {},
            "mcp": dict(self.mcps),
            "hook": {"hooks": tuple(self.hooks)} if self.hooks else {},
            "middleware": {"middleware": tuple(self.middlewares)} if self.middlewares else {},
            "channel": dict(self.channels),
            "message_filter": dict(self.message_filters),
            "conversation_event": dict(self.event_handlers),
        }

    def hook(
        self,
        point: HookPoint,
        handler: HookHandler,
        *,
        priority: int = 100,
        timeout_seconds: float = 30,
    ) -> None:
        if point not in HOOK_POINTS:
            raise ValueError(f"Unknown Agent lifecycle hook: {point}")
        if not callable(handler) or not (
            iscoroutinefunction(handler) or iscoroutinefunction(type(handler).__call__)
        ):
            raise TypeError("Lifecycle hooks must be async functions")
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Hook timeout must be greater than zero and at most 300 seconds")
        if any(item.point == point and item.handler == handler for item in self.hooks):
            raise ValueError("The same callback cannot be registered twice at one hook point")
        self.hooks.append(HookRegistration(point, handler, priority, timeout_seconds))

    def channel(
        self,
        kind: str,
        channel_cls: type[ChannelAdapter],
        *,
        label: str = "",
        icon: str = "",
        intro_url: str = "",
        fields: list[dict[str, Any] | ConfigField] | None = None,
    ) -> None:
        if not kind.isidentifier() or kind in self.channels:
            raise ValueError("Channel kinds must be unique identifiers")
        if not isinstance(channel_cls, type) or not issubclass(channel_cls, ChannelAdapter):
            raise TypeError("Register an opsmesh_sdk.channels.ChannelAdapter implementation class")
        self.channels[kind] = ChannelRegistration(
            kind, channel_cls, label or kind, icon, intro_url, _fields(fields)
        )


def _fields(values: list[dict[str, Any] | ConfigField] | None) -> tuple[ConfigField, ...]:
    fields = tuple(ConfigField.model_validate(value) for value in values or [])
    if len({item.name for item in fields}) != len(fields):
        raise ValueError("Configuration field names must be unique")
    return fields


def resolve_config(
    fields: tuple[ConfigField, ...], values: Mapping[str, object]
) -> Mapping[str, object]:
    if set(values) - {item.name for item in fields}:
        raise ValueError("Unknown plugin configuration field")
    result: dict[str, object] = {}
    for item in fields:
        value = values.get(item.name, item.default)
        if value is None:
            if item.required:
                raise ValueError(f"Configuration field {item.name} is required")
            continue
        valid = (
            isinstance(value, str)
            if item.type in {"text", "password", "select"}
            else type(value) in {int, float}
            if item.type == "number"
            else isinstance(value, bool)
        )
        if not valid or (item.type == "select" and value not in item.options):
            raise ValueError(f"Invalid configuration field {item.name}")
        result[item.name] = value
    return MappingProxyType(result)
