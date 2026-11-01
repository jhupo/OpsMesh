"""Ordinary provider functions usable with Depends in trusted plugins."""

from typing import Any, cast

from opsmesh.framework.agents import AgentExecutionContext, get_current_execution_context
from opsmesh.framework.agents.resources import SandboxProviderRegistry
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccountManagement
from opsmesh.modules.agents.api import AgentFactoryRegistry
from opsmesh.modules.agents.management import AgentManagement
from opsmesh.modules.audit.management import AuditManagement
from opsmesh.modules.bots.api import BotAccounts, MessageDelivery
from opsmesh.modules.capabilities.api import (
    CodeToolRegistry,
    ToolInvoker,
)
from opsmesh.modules.capabilities.embedding_registry import EmbeddingProviderRegistry
from opsmesh.modules.capabilities.knowledge_management import KnowledgeManagement
from opsmesh.modules.capabilities.management import CapabilityManagement
from opsmesh.modules.capabilities.mcp_management import MCPManagement
from opsmesh.modules.capabilities.script_management import ScriptManagement
from opsmesh.modules.capabilities.skill_management import SkillManagement
from opsmesh.modules.configuration.management import ConfigurationManagement
from opsmesh.modules.memory.management import MemoryManagement
from opsmesh.modules.models.management import ModelManagement
from opsmesh.modules.notifications.management import NotificationManagement
from opsmesh.modules.operations.api import PluginOperations
from opsmesh.modules.operations.outbox import OutboxManagement
from opsmesh.modules.runtime.api import RuntimeExtensions
from opsmesh.modules.runtime.management import RunManagement
from opsmesh.modules.scheduling.api import SchedulingService
from opsmesh.modules.sessions.management import ConversationManagement
from opsmesh.modules.triggers.api import TriggerService
from opsmesh.modules.usage.management import UsageManagement
from opsmesh.modules.workspaces.management import WorkspaceManagement

_providers: dict[str, Any] = {}


def bind_providers(providers: dict[str, Any]) -> None:
    """Composition-root binding performed before plugin import, once per application."""
    _providers.clear()
    _providers.update(providers)


def _get(name: str) -> Any:
    if name not in _providers:
        raise OpsMeshError(
            "CONTEXT_UNAVAILABLE", "Initialize the application before loading plugins"
        )
    return _providers[name]


def get_accounts() -> AccountManagement:
    return cast(AccountManagement, _get("accounts"))


def get_agents() -> AgentManagement:
    return cast(AgentManagement, _get("agents"))


def get_agent_factories() -> AgentFactoryRegistry:
    return cast(AgentFactoryRegistry, _get("agent_factories"))


def get_runtime() -> RunManagement:
    return cast(RunManagement, _get("runtime"))


def get_runtime_extensions() -> RuntimeExtensions:
    return cast(RuntimeExtensions, _get("runtime_extensions"))


def get_sandbox_providers() -> SandboxProviderRegistry:
    return cast(SandboxProviderRegistry, _get("sandbox_providers"))


def get_code_tools() -> CodeToolRegistry:
    return cast(CodeToolRegistry, _get("code_tools"))


def get_capabilities() -> CapabilityManagement:
    return cast(CapabilityManagement, _get("capabilities"))


def get_script_tools() -> ScriptManagement:
    return cast(ScriptManagement, _get("script_tools"))


def get_workspaces() -> WorkspaceManagement:
    return cast(WorkspaceManagement, _get("workspaces"))


def get_memory() -> MemoryManagement:
    return cast(MemoryManagement, _get("memory"))


def get_usage() -> UsageManagement:
    return cast(UsageManagement, _get("usage"))


def get_run_context() -> AgentExecutionContext:
    return get_current_execution_context()


def get_audit() -> AuditManagement:
    return cast(AuditManagement, _get("audit"))


def get_bots() -> BotAccounts:
    return cast(BotAccounts, _get("bots"))


def get_message_delivery() -> MessageDelivery:
    return cast(MessageDelivery, _get("message_delivery"))


def get_configuration() -> ConfigurationManagement:
    return cast(ConfigurationManagement, _get("configuration"))


def get_models() -> ModelManagement:
    return cast(ModelManagement, _get("models"))


def get_sessions() -> ConversationManagement:
    return cast(ConversationManagement, _get("sessions"))


def get_scheduling() -> SchedulingService:
    return cast(SchedulingService, _get("scheduling"))


def get_triggers() -> TriggerService:
    return cast(TriggerService, _get("triggers"))


def get_tool_invoker() -> ToolInvoker:
    return cast(ToolInvoker, _get("tool_invoker"))


def get_skills() -> SkillManagement:
    return cast(SkillManagement, _get("skills"))


def get_knowledge() -> KnowledgeManagement:
    return cast(KnowledgeManagement, _get("knowledge"))


def get_mcp_catalog() -> MCPManagement:
    return cast(MCPManagement, _get("mcp_catalog"))


def get_notifications() -> NotificationManagement:
    return cast(NotificationManagement, _get("notifications"))


def get_outbox_management() -> "OutboxManagement":
    return cast("OutboxManagement", _get("outbox_management"))


def get_plugin_management() -> PluginOperations:
    return cast(PluginOperations, _get("plugin_management"))


def get_embedding_registry() -> EmbeddingProviderRegistry:
    return cast(EmbeddingProviderRegistry, _get("embedding_registry"))
