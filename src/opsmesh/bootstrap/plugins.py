"""Register feature adapters and compose a generic plugin host for each process."""

import asyncio
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from opsmesh_sdk import PluginContext

from opsmesh.agents.plugins import agent_plugin_registry
from opsmesh.agents.plugins.adapters import (
    HookAdapter,
    MCPAdapter,
    MiddlewareAdapter,
    SkillAdapter,
    ToolAdapter,
)
from opsmesh.agents.plugins.skills import skill_packages
from opsmesh.config import Settings
from opsmesh.gateway.channels.plugins import ChannelAdapter
from opsmesh.gateway.channels.registry import ChannelRegistry
from opsmesh.gateway.management.events import ConversationEventAdapter
from opsmesh.gateway.messaging.filters import MessageFilterAdapter
from opsmesh.plugins.base import PreparedResource
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.packages import PluginPackages
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.resources.layout import StorageLayout
from opsmesh.shared.security.encryption import SecretCipher


def plugin_views(
    contexts: tuple[PluginContext, ...], resources: Mapping[str, PreparedResource]
) -> dict[str, Any]:
    tools = {
        (resource.plugin_id, resource.name): resource.value
        for resource in resources.values()
        if resource.kind == "tool"
    }
    agents = agent_plugin_registry(contexts, prepared_tools=tools)
    archives = {
        (resource.plugin_id, resource.name): resource.value
        for resource in resources.values()
        if resource.kind == "skill"
    }
    return {
        "agents": agents,
        "channels": ChannelRegistry(contexts),
        "skill_packages": skill_packages(agents, archives=archives),
    }


def create_plugin_manager(
    settings: Settings,
    cipher: SecretCipher,
    *,
    role: str,
    layout: StorageLayout | None = None,
) -> PluginManager:
    registry = PluginRegistry()
    for adapter in (
        ToolAdapter(),
        SkillAdapter(),
        MCPAdapter(),
        HookAdapter(),
        MiddlewareAdapter(),
        ChannelAdapter(),
        MessageFilterAdapter(),
        ConversationEventAdapter(),
    ):
        registry.register(adapter)
    layout = layout or StorageLayout(Path(settings.storage_root), settings.plugins_root)
    return PluginManager(
        PluginPackages(layout.plugin_packages),
        layout.plugin_data,
        registry,
        plugin_views,
        role=role,
        cipher=cipher,
    )


def watch_plugins(manager: PluginManager, resources: AsyncExitStack) -> None:
    task = asyncio.create_task(manager.watch(), name=f"opsmesh-plugins-{manager.role}")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    resources.push_async_callback(stop)
