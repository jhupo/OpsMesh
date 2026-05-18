from fastapi import APIRouter

from backend.app.api.routes.approvals import router as approvals_router
from backend.app.api.routes.capabilities import router as capabilities_router
from backend.app.api.routes.domains import router as domains_router
from backend.app.api.routes.exports import router as exports_router
from backend.app.api.routes.files import router as files_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.marketplace import router as marketplace_router
from backend.app.api.routes.model_providers import router as model_providers_router
from backend.app.api.routes.operations import router as operations_router
from backend.app.api.routes.runtimes import router as runtimes_router
from backend.app.api.routes.self_hosted import router as self_hosted_router
from backend.app.api.routes.workspace_resources import router as workspace_resources_router
from backend.app.api.routes.workspaces import router as workspaces_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(workspaces_router)
api_router.include_router(marketplace_router)
api_router.include_router(model_providers_router)
api_router.include_router(workspace_resources_router)
api_router.include_router(files_router)
api_router.include_router(approvals_router)
api_router.include_router(domains_router)
api_router.include_router(capabilities_router)
api_router.include_router(runtimes_router)
api_router.include_router(self_hosted_router)
api_router.include_router(operations_router)
api_router.include_router(exports_router)
