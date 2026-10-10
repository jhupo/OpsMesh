from fastapi import APIRouter

from opsmesh.bootstrap.team_routes import router as team_resources_router
from opsmesh.orchestration.tasks.routes.router import router as task_resources_router
from opsmesh.resources.knowledge.routes import router as knowledge_resources_router
from opsmesh.resources.memory.routes import router as memory_resources_router

router = APIRouter()
router.include_router(memory_resources_router)
router.include_router(knowledge_resources_router)
router.include_router(team_resources_router)
router.include_router(task_resources_router)
