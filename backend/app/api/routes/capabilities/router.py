from fastapi import APIRouter

from backend.app.api.routes.capabilities.base import router as capability_base_router
from backend.app.api.routes.capabilities.catalog import router as capability_catalog_router
from backend.app.api.routes.capabilities.governance import (
    router as capability_governance_router,
)
from backend.app.api.routes.capabilities.mcp import router as capability_mcp_router
from backend.app.api.routes.capabilities.plugins import router as plugins_router
from backend.app.api.routes.capabilities.skills import (
    router as capability_workspace_skills_router,
)

router = APIRouter()
router.include_router(plugins_router)
router.include_router(capability_base_router)
router.include_router(capability_catalog_router)
router.include_router(capability_governance_router)
router.include_router(capability_workspace_skills_router)
router.include_router(capability_mcp_router)
