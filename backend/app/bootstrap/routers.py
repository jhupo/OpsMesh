from fastapi import APIRouter

from backend.app.agents.messages.routes import router as agent_messages_router
from backend.app.agents.profiles.routes import router as workspace_agents_router
from backend.app.agents.providers.capability_routes import (
    router as model_provider_capabilities_router,
)
from backend.app.agents.providers.routes import router as model_providers_router
from backend.app.agents.sessions.routes import router as workspace_agent_sessions_router
from backend.app.bootstrap.capability_routes import router as capabilities_router
from backend.app.bootstrap.platform_routes import router as admin_router
from backend.app.bootstrap.workspace_routes import router as workspace_resources_router
from backend.app.capabilities.marketplace.public_routes import router as marketplace_router
from backend.app.capabilities.plugins.runtime_routes import router as plugin_runtime_router
from backend.app.governance.costs.routes import router as costs_router
from backend.app.identity.auth.routes import router as auth_router
from backend.app.identity.authorization.routes import router as resource_access_router
from backend.app.identity.invitations.routes import router as invitations_router
from backend.app.messaging.notifications.routes import router as notifications_router
from backend.app.orchestration.approvals.routes import router as approvals_router
from backend.app.orchestration.automations.routes import router as automations_router
from backend.app.orchestration.definitions.routes import router as orchestrations_router
from backend.app.orchestration.runs.routes import router as workspace_runs_router
from backend.app.orchestration.scheduling.routes import router as scheduled_jobs_router
from backend.app.orchestration.webhooks.routes import router as webhooks_router
from backend.app.platform.health.metrics_routes import router as metrics_router
from backend.app.platform.health.routes import router as health_router
from backend.app.resources.files.routes import router as files_router
from backend.app.resources.transfers.routes.router import router as exports_router
from backend.app.runtime.instances.routes import router as runtimes_router
from backend.app.runtime.operations.routes.router import router as operations_router
from backend.app.runtime.self_hosted.routes.router import router as self_hosted_router
from backend.app.runtime.spaces.routes import router as runtime_spaces_router
from backend.app.workspaces.domain_items.routes import router as domains_router
from backend.app.workspaces.management.routes import router as workspaces_router
from backend.app.workspaces.projects.routes import router as projects_router

api_router = APIRouter()
api_router.include_router(admin_router)
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(invitations_router)
api_router.include_router(resource_access_router)
api_router.include_router(metrics_router, tags=["metrics"])
api_router.include_router(workspaces_router)
api_router.include_router(marketplace_router)
api_router.include_router(model_providers_router)
api_router.include_router(model_provider_capabilities_router)
api_router.include_router(workspace_agents_router)
api_router.include_router(workspace_agent_sessions_router)
api_router.include_router(workspace_runs_router)
api_router.include_router(workspace_resources_router)
api_router.include_router(agent_messages_router)
api_router.include_router(notifications_router)
api_router.include_router(orchestrations_router)
api_router.include_router(scheduled_jobs_router)
api_router.include_router(files_router)
api_router.include_router(projects_router)
api_router.include_router(approvals_router)
api_router.include_router(domains_router)
api_router.include_router(capabilities_router)
api_router.include_router(costs_router)
api_router.include_router(runtime_spaces_router)
api_router.include_router(runtimes_router)
api_router.include_router(self_hosted_router)
api_router.include_router(operations_router)
api_router.include_router(exports_router)
api_router.include_router(webhooks_router)
api_router.include_router(automations_router)
api_router.include_router(plugin_runtime_router)
