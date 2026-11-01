"""Compose protected command services alongside trusted runtime collaborators."""

from typing import TYPE_CHECKING

from opsmesh.application.resource_references import PlatformResourceReferences
from opsmesh.framework.plugin.manager import manager
from opsmesh.modules.accounts.internal.management import AuthorizedAccountManagement
from opsmesh.modules.agents.management import AgentManagement
from opsmesh.modules.audit.management import AuditManagement
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
from opsmesh.modules.operations.outbox import OutboxManagement
from opsmesh.modules.operations.plugins import PluginDeploymentManagement
from opsmesh.modules.runtime.management import RunManagement
from opsmesh.modules.sessions.management import ConversationManagement
from opsmesh.modules.usage.management import UsageManagement
from opsmesh.modules.workspaces.management import WorkspaceManagement

if TYPE_CHECKING:
    from .services import OpsMeshServices


def compose_management(services: "OpsMeshServices") -> None:
    services.resource_references = PlatformResourceReferences(
        accounts=services.accounts,
        agents=services.agents,
        runtime=services.runtime,
        database=services.database,
    )
    services.account_management = AuthorizedAccountManagement(services.accounts)
    services.run_management = RunManagement(services.runtime, services.accounts, services.audit)
    services.conversation_management = ConversationManagement(
        services.sessions,
        services.accounts,
        services.agents,
        services.authorization.workspace,
        services.audit,
    )
    services.agent_management = AgentManagement(services.agents, services.accounts, services.audit)
    services.audit_management = AuditManagement(services.audit, services.accounts)
    services.configuration_management = ConfigurationManagement(
        services.configuration, services.accounts, services.audit
    )
    services.usage_management = UsageManagement(
        services.usage, services.accounts, services.models, services.audit
    )
    services.model_management = ModelManagement(
        services.models,
        services.authorization.check,
        services.audit,
        references=services.resource_references.lookup,
        accounts=services.accounts,
        credentials=services.model_credentials,
        personal_model_allowed_hosts=services.settings.personal_model_allowed_hosts,
    )
    services.workspace_management = WorkspaceManagement(
        services.workspaces,
        services.authorization.workspace,
        services.audit,
        services.resource_references.snapshots,
        owners=services.workspace_owners,
        authorize_creation=services.authorization.check,
    )
    services.memory_management = MemoryManagement(
        services.memory, services.audit, validate_actor=services.authorization.active_actor
    )
    services.knowledge_management = KnowledgeManagement(services.knowledge, services.audit)
    services.notification_management = NotificationManagement(services.notifications)
    services.outbox_management = OutboxManagement(
        services.database, services.accounts, services.audit
    )
    services.plugin_management = PluginDeploymentManagement(
        services.database, services.accounts, services.audit, manager
    )

    services.skill_management = SkillManagement(
        services.skills,
        services.authorization.check,
        services.authorization.workspace,
        services.audit,
    )
    services.script_management = ScriptManagement(
        services.scripts,
        services.authorization.check,
        services.audit,
    )
    services.mcp_management = MCPManagement(
        services.mcp_catalog,
        services.mcp,
        services.authorization.check,
        services.audit,
    )
    services.capability_management = CapabilityManagement(
        services.capabilities,
        services.skill_management,
        services.mcp_management,
        services.script_management,
        services.authorization.check,
        services.audit,
        references=services.resource_references.lookup,
    )
    services.embedding_registry = EmbeddingProviderRegistry(services.knowledge)
