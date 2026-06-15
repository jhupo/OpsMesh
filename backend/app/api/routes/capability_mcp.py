from fastapi import APIRouter

from backend.app.api.routes.capability_mcp_credentials import (
    router as mcp_credentials_router,
)
from backend.app.api.routes.capability_mcp_observability import (
    router as mcp_observability_router,
)
from backend.app.api.routes.capability_mcp_servers import router as mcp_servers_router

router = APIRouter()
router.include_router(mcp_servers_router)
router.include_router(mcp_credentials_router)
router.include_router(mcp_observability_router)
