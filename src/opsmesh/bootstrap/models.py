"""Explicit ORM registration shared by API, workers, migrations and tests."""

from importlib import import_module

from sqlalchemy import MetaData
from sqlalchemy.orm import configure_mappers

from opsmesh.shared.db.base import Base

_MODEL_MODULES = (
    "opsmesh.platform.data_migrations.models",
    "opsmesh.runtime.queues.models",
    "opsmesh.orchestration.conversations.models",
    "opsmesh.runtime.operations.models",
    "opsmesh.governance.policies.models",
    "opsmesh.messaging.email.models",
    "opsmesh.platform.updates.models",
    "opsmesh.identity.auth.models",
    "opsmesh.identity.authorization.models",
    "opsmesh.identity.invitations.models",
    "opsmesh.identity.users.models",
    "opsmesh.orchestration.webhooks.models",
    "opsmesh.orchestration.automations.models",
    "opsmesh.governance.security_events.models",
    "opsmesh.resources.memory.models",
    "opsmesh.agents.messages.models",
    "opsmesh.agents.profiles.models",
    "opsmesh.agents.providers.models",
    "opsmesh.agents.sessions.models",
    "opsmesh.capabilities.marketplace.models",
    "opsmesh.capabilities.plugins.models",
    "opsmesh.capabilities.catalog.models",
    "opsmesh.capabilities.mcp.models",
    "opsmesh.capabilities.references.models",
    "opsmesh.capabilities.skills.models",
    "opsmesh.resources.knowledge.models",
    "opsmesh.orchestration.approvals.models",
    "opsmesh.orchestration.definitions.models",
    "opsmesh.orchestration.runs.subworkflow_models",
    "opsmesh.orchestration.runs.models",
    "opsmesh.orchestration.tasks.models",
    "opsmesh.orchestration.planning.attempt_models",
    "opsmesh.workspaces.domain_items.models",
    "opsmesh.resources.transfers.models",
    "opsmesh.workspaces.projects.models",
    "opsmesh.resources.artifacts.models",
    "opsmesh.resources.files.models",
    "opsmesh.teams.management.models",
    "opsmesh.workspaces.management.models",
    "opsmesh.workspaces.members.models",
    "opsmesh.workspaces.quotas.models",
    "opsmesh.governance.audit.models",
    "opsmesh.governance.costs.models",
    "opsmesh.messaging.notifications.models",
    "opsmesh.runtime.instances.models",
    "opsmesh.runtime.spaces.models",
    "opsmesh.runtime.workers.models",
    "opsmesh.runtime.self_hosted.models",
    "opsmesh.orchestration.scheduling.models",
)


def register_models() -> MetaData:
    for module_name in _MODEL_MODULES:
        import_module(module_name)
    configure_mappers()
    from opsmesh.orchestration.conversations.dispatch import register_task_notifications

    register_task_notifications()
    return Base.metadata
