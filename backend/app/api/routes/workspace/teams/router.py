from fastapi import APIRouter

from backend.app.api.routes.workspace.teams.execution import router as execution_router
from backend.app.api.routes.workspace.teams.members import router as member_router
from backend.app.api.routes.workspace.teams.overview import router as overview_router
from backend.app.api.routes.workspace.teams.runtime import router as runtime_router
from backend.app.api.routes.workspace.teams.sessions import router as session_router

router = APIRouter()
router.include_router(overview_router)
router.include_router(session_router)
router.include_router(runtime_router)
router.include_router(execution_router)
router.include_router(member_router)
