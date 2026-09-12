from fastapi import APIRouter

from backend.app.api.routes.admin.router import router as admin_router
from backend.app.api.routes.agents.messages import router as agent_messages_router
from backend.app.api.routes.agents.profiles import router as workspace_agents_router
from backend.app.api.routes.agents.provider_capabilities import (
    router as model_provider_capabilities_router,
)
from backend.app.api.routes.agents.providers import router as model_providers_router
from backend.app.api.routes.agents.sessions import (
    router as workspace_agent_sessions_router,
)
from backend.app.api.routes.capabilities.marketplace import router as marketplace_router
from backend.app.api.routes.capabilities.router import router as capabilities_router
from backend.app.api.routes.integrations.webhooks import router as webhooks_router
from backend.app.api.routes.operations.costs import router as costs_router
from backend.app.api.routes.operations.health import router as health_router
from backend.app.api.routes.operations.metrics import router as metrics_router
from backend.app.api.routes.operations.notifications import router as notifications_router
from backend.app.api.routes.operations.router import router as operations_router
from backend.app.api.routes.operations.runtime_spaces import router as runtime_spaces_router
from backend.app.api.routes.operations.runtimes import router as runtimes_router
from backend.app.api.routes.operations.scheduled_jobs import router as scheduled_jobs_router
from backend.app.api.routes.orchestration.approvals import router as approvals_router
from backend.app.api.routes.orchestration.definitions import router as orchestrations_router
from backend.app.api.routes.orchestration.runs import router as workspace_runs_router
from backend.app.api.routes.platform.auth import router as auth_router
from backend.app.api.routes.self_hosted.router import router as self_hosted_router
from backend.app.api.routes.workspace.domains import router as domains_router
from backend.app.api.routes.workspace.exports.router import router as exports_router
from backend.app.api.routes.workspace.files import router as files_router
from backend.app.api.routes.workspace.projects import router as projects_router
from backend.app.api.routes.workspace.router import router as workspace_resources_router
from backend.app.api.routes.workspace.workspaces import router as workspaces_router

api_router = APIRouter()
api_router.include_router(admin_router)
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
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
