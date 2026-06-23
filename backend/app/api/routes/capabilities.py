from fastapi import APIRouter

from backend.app.api.routes.capability_base import router as capability_base_router
from backend.app.api.routes.capability_governance import router as capability_governance_router
from backend.app.api.routes.capability_mcp import router as capability_mcp_router
from backend.app.api.routes.capability_workspace_skills import (
    router as capability_workspace_skills_router,
)

router = APIRouter()
router.include_router(capability_base_router)
router.include_router(capability_governance_router)
router.include_router(capability_workspace_skills_router)
router.include_router(capability_mcp_router)
