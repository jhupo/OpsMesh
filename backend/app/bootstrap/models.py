"""Explicit ORM registration shared by API, workers, migrations and tests."""

from importlib import import_module

from sqlalchemy import MetaData
from sqlalchemy.orm import configure_mappers

from backend.app.shared.db.base import Base

_MODEL_MODULES = (
    "backend.app.governance.policies.models",
    "backend.app.messaging.email.models",
    "backend.app.platform.updates.models",
    "backend.app.identity.auth.models",
    "backend.app.identity.authorization.models",
    "backend.app.identity.invitations.models",
    "backend.app.identity.users.models",
    "backend.app.orchestration.webhooks.models",
    "backend.app.orchestration.automations.models",
    "backend.app.governance.security_events.models",
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
    "backend.app.orchestration.approvals.models",
    "backend.app.orchestration.definitions.models",
    "backend.app.orchestration.runs.subworkflow_models",
    "backend.app.orchestration.runs.models",
    "backend.app.orchestration.tasks.models",
    "backend.app.orchestration.planning.attempt_models",
    "backend.app.workspaces.domain_items.models",
    "backend.app.resources.transfers.models",
    "backend.app.workspaces.projects.models",
    "backend.app.resources.artifacts.models",
    "backend.app.resources.files.models",
    "backend.app.teams.management.models",
    "backend.app.workspaces.management.models",
    "backend.app.workspaces.members.models",
    "backend.app.workspaces.quotas.models",
    "backend.app.governance.audit.models",
    "backend.app.governance.costs.models",
    "backend.app.messaging.notifications.models",
    "backend.app.runtime.instances.models",
    "backend.app.runtime.spaces.models",
    "backend.app.runtime.workers.models",
    "backend.app.runtime.self_hosted.models",
    "backend.app.orchestration.scheduling.models",
)


def register_models() -> MetaData:
    for module_name in _MODEL_MODULES:
        import_module(module_name)
    configure_mappers()
    return Base.metadata
