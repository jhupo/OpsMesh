from fastapi import APIRouter

from backend.app.api.routes.orchestration.tasks.router import router as task_resources_router
from backend.app.api.routes.workspace.knowledge import router as knowledge_resources_router
from backend.app.api.routes.workspace.memory import router as memory_resources_router
from backend.app.api.routes.workspace.teams.router import router as team_resources_router

router = APIRouter()
router.include_router(memory_resources_router)
router.include_router(knowledge_resources_router)
router.include_router(team_resources_router)
router.include_router(task_resources_router)
