from fastapi import APIRouter

from backend.app.capabilities.mcp.credential_routes import router as mcp_credentials_router
from backend.app.capabilities.mcp.managed_routes import router as managed_mcp_router
from backend.app.capabilities.mcp.observability_routes import router as mcp_observability_router
from backend.app.capabilities.mcp.server_routes import router as mcp_servers_router

router = APIRouter()
router.include_router(mcp_servers_router)
router.include_router(mcp_credentials_router)
router.include_router(mcp_observability_router)
router.include_router(managed_mcp_router)
