from fastapi import APIRouter

from backend.app.api.routes.workspace.base import router as base_router
from backend.app.api.routes.workspace.invites import router as invite_router
from backend.app.api.routes.workspace.members import router as member_router
from backend.app.api.routes.workspace.quotas import router as quota_router

router = APIRouter(tags=["workspaces"])
router.include_router(base_router)
router.include_router(invite_router)
router.include_router(member_router)
router.include_router(quota_router)
