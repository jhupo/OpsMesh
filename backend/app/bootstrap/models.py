"""Explicit ORM registration shared by API, workers, migrations and tests."""

from importlib import import_module

from sqlalchemy import MetaData
from sqlalchemy.orm import configure_mappers

from backend.app.core.db.base import Base

_MODEL_MODULES = (
    "backend.app.domains.platform.admin.models",
    "backend.app.domains.platform.updates.models",
    "backend.app.domains.access.models",
    "backend.app.domains.integrations.webhooks.models",
    "backend.app.domains.integrations.automation_models",
    "backend.app.observability.audit.security_models",
    "backend.app.domains.agents.memory.models",
    "backend.app.domains.agents.messages.models",
    "backend.app.domains.agents.profiles.models",
    "backend.app.domains.agents.providers.models",
    "backend.app.domains.agents.sessions.models",
    "backend.app.domains.capabilities.marketplace.models",
    "backend.app.domains.capabilities.catalog.models",
    "backend.app.domains.capabilities.mcp.models",
    "backend.app.domains.capabilities.resources.models",
    "backend.app.domains.capabilities.skills.models",
    "backend.app.domains.knowledge.models",
    "backend.app.domains.orchestration.approvals.models",
    "backend.app.domains.orchestration.models",
    "backend.app.domains.orchestration.runs.models",
    "backend.app.domains.orchestration.tasks.models",
    "backend.app.domains.orchestration.workflows.planning.attempt_models",
    "backend.app.domains.workspace.extensions.models",
    "backend.app.domains.workspace.data_transfer.models",
    "backend.app.domains.workspace.projects.models",
    "backend.app.domains.workspace.storage.artifact_models",
    "backend.app.domains.workspace.storage.models",
    "backend.app.domains.workspace.teams.models",
    "backend.app.domains.workspace.tenants.models",
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
