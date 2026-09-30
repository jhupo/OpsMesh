"""Explicit ORM registration shared by API, workers, migrations and tests."""

from importlib import import_module

from sqlalchemy import MetaData
from sqlalchemy.orm import configure_mappers

from backend.app.core.db.base import Base

_MODEL_MODULES = (
    "backend.app.domains.platform.admin.models",
    "backend.app.messaging.email.models",
    "backend.app.domains.platform.updates.models",
    "backend.app.identity.auth.models",
    "backend.app.identity.authorization.models",
    "backend.app.identity.invitations.models",
    "backend.app.identity.users.models",
    "backend.app.domains.integrations.webhooks.models",
    "backend.app.domains.integrations.automation_models",
    "backend.app.observability.audit.security_models",
    "backend.app.resources.memory.models",
    "backend.app.agents.messages.models",
    "backend.app.agents.profiles.models",
    "backend.app.agents.providers.models",
    "backend.app.agents.sessions.models",
    "backend.app.capabilities.marketplace.models",
    "backend.app.capabilities.plugins.models",
    "backend.app.capabilities.catalog.models",
    "backend.app.capabilities.mcp.models",
    "backend.app.capabilities.references.models",
    "backend.app.capabilities.skills.models",
    "backend.app.resources.knowledge.models",
    "backend.app.domains.orchestration.approvals.models",
    "backend.app.domains.orchestration.models",
    "backend.app.domains.orchestration.runs.models",
    "backend.app.domains.orchestration.tasks.models",
    "backend.app.domains.orchestration.workflows.planning.attempt_models",
    "backend.app.workspaces.domain_items.models",
    "backend.app.resources.transfers.models",
    "backend.app.workspaces.projects.models",
    "backend.app.resources.artifacts.models",
    "backend.app.resources.files.models",
    "backend.app.teams.management.models",
    "backend.app.workspaces.management.models",
    "backend.app.workspaces.members.models",
    "backend.app.workspaces.quotas.models",
    "backend.app.observability.audit.models",
    "backend.app.observability.costs.models",
    "backend.app.observability.notifications.models",
    "backend.app.runtime.environment.models",
    "backend.app.runtime.environment.spaces.models",
    "backend.app.runtime.workers.models",
    "backend.app.runtime.self_hosted.models",
    "backend.app.runtime.workers.scheduling.models",
)


def register_models() -> MetaData:
    for module_name in _MODEL_MODULES:
        import_module(module_name)
    configure_mappers()
    return Base.metadata
