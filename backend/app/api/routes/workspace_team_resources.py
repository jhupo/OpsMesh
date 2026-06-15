from fastapi import APIRouter

from backend.app.api.routes.workspace_team_execution_resources import router as execution_router
from backend.app.api.routes.workspace_team_member_resources import router as member_router
from backend.app.api.routes.workspace_team_overview_resources import router as overview_router
from backend.app.api.routes.workspace_team_runtime_resources import router as runtime_router
from backend.app.api.routes.workspace_team_session_resources import router as session_router

router = APIRouter()
router.include_router(overview_router)
router.include_router(session_router)
router.include_router(runtime_router)
router.include_router(execution_router)
router.include_router(member_router)
