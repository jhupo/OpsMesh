from fastapi import APIRouter

from backend.app.api.routes.workspace_task_resources import router as task_resources_router
from backend.app.api.routes.workspace_team_resources import router as team_resources_router

router = APIRouter()
router.include_router(team_resources_router)
router.include_router(task_resources_router)
