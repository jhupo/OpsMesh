from fastapi import APIRouter

from backend.app.api.routes.workspace_base_routes import router as base_router
from backend.app.api.routes.workspace_invite_routes import router as invite_router
from backend.app.api.routes.workspace_member_routes import router as member_router
from backend.app.api.routes.workspace_quota_routes import router as quota_router

router = APIRouter(tags=["workspaces"])
router.include_router(base_router)
router.include_router(invite_router)
router.include_router(member_router)
router.include_router(quota_router)
