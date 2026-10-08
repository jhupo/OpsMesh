from fastapi import APIRouter

from backend.app.teams.execution.routes import router as execution_router
from backend.app.teams.management.member_routes import router as member_router
from backend.app.teams.management.routes import router as overview_router
from backend.app.teams.sessions.routes import router as session_router
from backend.app.teams.sessions.runtime_routes import router as runtime_router

router = APIRouter()
router.include_router(overview_router)
router.include_router(session_router)
router.include_router(runtime_router)
router.include_router(execution_router)
router.include_router(member_router)
