from fastapi import APIRouter

from opsmesh.workspaces.management.dependencies import router as base_router
from opsmesh.workspaces.members.invitation_routes import router as invite_router
from opsmesh.workspaces.members.routes import router as member_router
from opsmesh.workspaces.quotas.routes import router as quota_router

router = APIRouter(tags=["workspaces"])
router.include_router(base_router)
router.include_router(invite_router)
router.include_router(member_router)
router.include_router(quota_router)
