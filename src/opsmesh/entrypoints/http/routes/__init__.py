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
    resource_sources,
    runs,
    scheduler,
    sessions,
    triggers,
    usage,
    workspaces,
)

routers = (
    accounts.router,
    agents.router,
    approvals.router,
    models.router,
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
)

__all__ = ["routers"]
