from fastapi import APIRouter

from backend.app.api.routes.approvals import router as approvals_router
from backend.app.api.routes.files import router as files_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.workspace_resources import router as workspace_resources_router
from backend.app.api.routes.workspaces import router as workspaces_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(workspaces_router)
api_router.include_router(workspace_resources_router)
api_router.include_router(files_router)
api_router.include_router(approvals_router)
