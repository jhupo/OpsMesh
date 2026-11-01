"""HTTP routes grouped by business ownership."""

from . import (
    accounts,
    agents,
    approvals,
    bots,
    capabilities,
    management,
    memory,
    models,
    notifications,
    operations,
    personal_models,
    resource_sources,
    runs,
    scheduler,
    sessions,
    triggers,
    updates,
    usage,
    workspaces,
)

routers = (
    accounts.router,
    agents.router,
    approvals.router,
    models.router,
    personal_models.router,
    sessions.router,
    runs.router,
    capabilities.router,
    resource_sources.router,
    workspaces.router,
    memory.router,
    bots.router,
    scheduler.router,
    management.router,
    usage.router,
    notifications.router,
    operations.router,
    triggers.router,
    updates.router,
)

__all__ = ["routers"]
