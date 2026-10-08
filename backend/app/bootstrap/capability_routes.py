from fastapi import APIRouter

from backend.app.capabilities.catalog.dependencies import router as capability_base_router
from backend.app.capabilities.catalog.routes import router as capability_catalog_router
from backend.app.capabilities.governance.routes import router as capability_governance_router
from backend.app.capabilities.mcp.routes import router as capability_mcp_router
from backend.app.capabilities.plugins.routes import router as plugins_router
from backend.app.capabilities.skills.routes import router as capability_workspace_skills_router

router = APIRouter()
router.include_router(plugins_router)
router.include_router(capability_base_router)
router.include_router(capability_catalog_router)
router.include_router(capability_governance_router)
router.include_router(capability_workspace_skills_router)
router.include_router(capability_mcp_router)
