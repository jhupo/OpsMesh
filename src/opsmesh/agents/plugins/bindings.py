"""Explicit plugin-to-Agent bindings; the caller supplies authorized tool execution."""

from __future__ import annotations

import json
import stat
from collections.abc import Awaitable, Callable, Collection, Mapping
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from agents import FunctionTool, Tool
from agents.mcp import MCPServer
from agents.sandbox.entries import BaseEntry, Dir, File
from agents.tool_context import ToolContext
from jsonschema import Draft202012Validator
from opsmesh_sdk.hooks import HookRegistration
from opsmesh_sdk.registration import resolve_config
from pydantic import JsonValue

from opsmesh.agents import AssemblyBindings
from opsmesh.agents.config.manifest import EntryRef, relative_path
from opsmesh.agents.config.mcp import MCPServerRef
from opsmesh.agents.plugins.hooks import PluginAgentHooks
from opsmesh.agents.plugins.mcp import PluginMCPConnector, connect_plugin_mcp
from opsmesh.agents.skills import SkillInfo
from opsmesh.shared.serialization import payload_digest

if TYPE_CHECKING:
    from opsmesh.agents.plugins.registry import InstalledTool, PluginRegistry

PluginToolInvoker = Callable[
    [str, str, ToolContext[Any], dict[str, Any], Mapping[str, object]], Awaitable[object]
]
PluginToolVisibility = Callable[[str, str], Awaitable[bool]]
PLUGIN_HOOKS_ID = "opsmesh.plugins.hooks"


@dataclass(frozen=True)
class PluginAgentContributions:
    tool_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    hook_ids: tuple[str, ...]
    mcp_ids: tuple[str, ...] = ()


def _tool_factory(
    plugin_id: str,
    installed: InstalledTool,
    invoke: PluginToolInvoker,
    defaults: Mapping[str, object],
    visible: PluginToolVisibility | None,
) -> Callable[[dict[str, JsonValue]], Tool]:
    def factory(config: dict[str, JsonValue]) -> Tool:
        # Agent binding configuration is validated against the plugin's declared
        # fields and installation defaults. Forward only explicit overrides so
        # the dispatcher can revalidate against the current installation config.
        resolve_config(installed.registration.config_fields, {**defaults, **config})
        binding_config = deepcopy(config)

        async def call(context: ToolContext[Any], payload: str) -> object:
            arguments = json.loads(payload)
            Draft202012Validator(installed.schema.params_json_schema).validate(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Plugin tool arguments must be an object")
            # Authorization, invocation context, timeout and effects belong to the
            # host dispatcher; never call the plugin function directly here.
            value = await invoke(
                plugin_id,
                installed.registration.name,
                context,
                arguments,
                deepcopy(binding_config),
            )
            return installed.result.dump_python(
                installed.result.validate_python(value), mode="json"
            )

        async def enabled(context: Any, agent: Any) -> bool:
            return await visible(plugin_id, installed.registration.name) if visible else True

        return FunctionTool(
            is_enabled=enabled,
            name=installed.describe(plugin_id).name,
            description=installed.schema.description or "",
            params_json_schema=deepcopy(installed.schema.params_json_schema),
            on_invoke_tool=call,
            strict_json_schema=False,
            needs_approval=installed.registration.requires_approval,
        )

    return factory


def _skill_directory(path: Path) -> Dir:
    size = 0
    count = 0

    def collect(directory: Path) -> Dir:
        nonlocal size, count
        children: dict[str, BaseEntry] = {}
        for file in sorted(directory.iterdir()):
            count += 1
            if count > 10_000:
                raise ValueError("Plugin skill exceeds 10000 entries")
            mode = file.lstat().st_mode
            destination = relative_path(file.name)
            if stat.S_ISDIR(mode):
                children[destination] = collect(file)
            elif stat.S_ISREG(mode):
                size += file.stat().st_size
                if size > 200_000_000:
                    raise ValueError("Plugin skill exceeds 200 MB")
                children[destination] = File(content=file.read_bytes())
            else:
                raise ValueError("Plugin skills contain only regular files and directories")
        return Dir(children=children)

    return collect(path)


def _hook_factory(
    registrations: tuple[HookRegistration, ...],
) -> Callable[[dict[str, JsonValue]], PluginAgentHooks]:
    def factory(config: dict[str, JsonValue]) -> PluginAgentHooks:
        if config:
            raise ValueError("Lifecycle hook groups do not accept per-Agent configuration")
        return PluginAgentHooks(registrations)

    return factory


def _skill_description(content: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            metadata = yaml.safe_load("\n".join(lines[1:index]))
            if not isinstance(metadata, dict):
                raise ValueError("Skill frontmatter must be a mapping")
            description = metadata.get("description", "")
            if not isinstance(description, str):
                raise ValueError("Skill description must be a string")
            return description
    raise ValueError("Skill frontmatter is missing its closing delimiter")


def register_agent_plugins(
    registry: PluginRegistry,
    bindings: AssemblyBindings,
    *,
    invoke_tool: PluginToolInvoker,
    visible: PluginToolVisibility | None = None,
    plugin_ids: Collection[str] | None = None,
    include_admin_mcp: bool = False,
    connect_mcp: PluginMCPConnector = connect_plugin_mcp,
) -> PluginAgentContributions:
    """Register a host-authorized selection into fresh per-caller assembly bindings.

    invoke_tool must reauthorize each call and supply the current InvocationContext,
    stable operation identity, timeout and effect receipts. Temporal dispatches tool
    calls and lifecycle notifications through Activities.
    """
    available = set(registry.plugins)
    selected_ids = available if plugin_ids is None else set(plugin_ids)
    unknown = selected_ids - available
    if unknown:
        raise KeyError(f"Unknown plugin IDs: {', '.join(sorted(unknown))}")
    selected = sorted(selected_ids)
    contexts = [registry.plugins[identifier] for identifier in selected]
    tools = []
    skills = []
    hooks: list[tuple[str, tuple[HookRegistration, ...]]] = []
    entries: dict[str, BaseEntry] = {}
    mcps = {
        f"{context.manifest.id}/mcp/{name}": (context, registration)
        for context in contexts
        for name, registration in context.mcps.items()
        if include_admin_mcp or not registration.admin_only
    }
    for context in contexts:
        identifier = context.manifest.id
        for name in sorted(context.tools):
            installed = registry.tools[identifier, name]
            defaults = context.config.get("tools", {})
            if not isinstance(defaults, dict) or not isinstance(defaults.get(name, {}), dict):
                raise ValueError("Installed tool configuration must be an object")
            tools.append(
                (
                    registry.describe_tool(identifier, name),
                    _tool_factory(
                        identifier,
                        installed,
                        invoke_tool,
                        deepcopy(defaults.get(name, {})),
                        visible,
                    ),
                )
            )
        if context.skills_directory is not None:
            for directory in sorted(context.skills_directory.iterdir()):
                if directory.is_symlink() or not directory.is_dir():
                    raise ValueError("A plugin skill must be a regular directory")
                relative_path(directory.name)
                entry_id = f"{identifier}/skills/{directory.name}"
                entry = _skill_directory(directory)
                content = entry.children.get("SKILL.md")
                if not isinstance(content, File):
                    raise ValueError("A plugin skill must contain a regular SKILL.md")
                entries[entry_id] = entry
                skills.append(
                    SkillInfo(
                        id=entry_id,
                        name=f"{identifier}__{directory.name}",
                        description=_skill_description(_decode_skill(content.content)),
                        source=f"plugin:{identifier}",
                        directory=EntryRef(entry_id=entry_id),
                    )
                )
        if context.hooks:
            hooks.append((f"{identifier}/hooks", tuple(context.hooks)))

    # Prepare everything before publishing any registrations, so an invalid
    # Skill or duplicate ID cannot leave a partially populated caller view.
    tool_ids = {info.id for info in bindings.tools.list()}
    skill_ids = {info.id for info in bindings.skills.list()}
    hook_ids = set(bindings.callbacks.list("agent_hooks"))
    if tool_ids & {info.id for info, _ in tools} or skill_ids & {info.id for info in skills}:
        raise ValueError("Plugin tools or skills are already registered")
    if hooks:
        hooks.append((PLUGIN_HOOKS_ID, tuple(item for _, items in hooks for item in items)))
    if hook_ids & {identifier for identifier, _ in hooks}:
        raise ValueError("Plugin hooks are already registered")

    for info, tool_factory in tools:
        bindings.tools.register(info, tool_factory)
    for skill in skills:
        bindings.skills.register(skill)
    for identifier, registrations in hooks:
        bindings.callbacks.register(
            "agent_hooks",
            identifier,
            _hook_factory(registrations),
            revision=payload_digest(
                {
                    item.manifest.id: {
                        "version": item.manifest.version,
                        "config": dict(item.config),
                    }
                    for item in contexts
                    if identifier == PLUGIN_HOOKS_ID or identifier == f"{item.manifest.id}/hooks"
                }
            ),
        )

    fallback = bindings.entry_resolver

    def resolve_entry(reference: EntryRef) -> BaseEntry:
        entry = entries.get(reference.entry_id)
        return fallback(reference) if entry is None else entry.model_copy(deep=True)

    bindings.entry_resolver = resolve_entry
    fallback_mcp = bindings.mcp_resolver

    async def resolve_plugin_mcp(
        reference: MCPServerRef,
        options: dict[str, Any],
        lifespan: AsyncExitStack,
    ) -> MCPServer:
        item = mcps.get(reference.connection_id)
        if item is not None:
            return await connect_mcp(*item, reference.config, options, lifespan)
        if fallback_mcp is None:
            raise ValueError("MCP resource is not available in this authorized plugin selection")
        return await fallback_mcp(reference, options, lifespan)

    if mcps:
        bindings.mcp_resolver = resolve_plugin_mcp
    return PluginAgentContributions(
        tuple(info.id for info, _ in tools),
        tuple(info.id for info in skills),
        tuple(identifier for identifier, _ in hooks),
        tuple(sorted(mcps)),
    )


def _decode_skill(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Plugin skill files must be UTF-8") from exc
