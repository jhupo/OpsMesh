from fastapi import APIRouter

from opsmesh.capabilities.mcp.credential_routes import router as mcp_credentials_router
from opsmesh.capabilities.mcp.managed_routes import router as managed_mcp_router
from opsmesh.capabilities.mcp.observability_routes import router as mcp_observability_router
from opsmesh.capabilities.mcp.server_routes import router as mcp_servers_router

router = APIRouter()
router.include_router(mcp_servers_router)
router.include_router(mcp_credentials_router)
router.include_router(mcp_observability_router)
router.include_router(managed_mcp_router)
