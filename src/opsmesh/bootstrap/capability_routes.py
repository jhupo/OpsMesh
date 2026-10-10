from fastapi import APIRouter

from opsmesh.capabilities.catalog.dependencies import router as capability_base_router
from opsmesh.capabilities.catalog.routes import router as capability_catalog_router
from opsmesh.capabilities.governance.routes import router as capability_governance_router
from opsmesh.capabilities.mcp.routes import router as capability_mcp_router
from opsmesh.capabilities.plugins.routes import router as plugins_router
from opsmesh.capabilities.skills.routes import router as capability_workspace_skills_router

router = APIRouter()
router.include_router(plugins_router)
router.include_router(capability_base_router)
router.include_router(capability_catalog_router)
router.include_router(capability_governance_router)
router.include_router(capability_workspace_skills_router)
router.include_router(capability_mcp_router)
