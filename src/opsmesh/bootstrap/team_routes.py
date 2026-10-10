from fastapi import APIRouter

from opsmesh.teams.execution.routes import router as execution_router
from opsmesh.teams.management.member_routes import router as member_router
from opsmesh.teams.management.routes import router as overview_router
from opsmesh.teams.sessions.routes import router as session_router
from opsmesh.teams.sessions.runtime_routes import router as runtime_router

router = APIRouter()
router.include_router(overview_router)
router.include_router(session_router)
router.include_router(runtime_router)
router.include_router(execution_router)
router.include_router(member_router)
