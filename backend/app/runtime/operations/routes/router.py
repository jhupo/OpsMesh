from fastapi import APIRouter

from backend.app.agents.providers.operation_routes import router as providers_router
from backend.app.orchestration.scheduling.operation_routes import router as scheduler_router
from backend.app.runtime.operations.routes.events import router as events_router
from backend.app.runtime.operations.routes.overview import router as overview_router
from backend.app.runtime.operations.routes.timeline import router as timeline_router
from backend.app.runtime.queues.routes import router as queue_router
from backend.app.runtime.workers.routes import router as workers_router

router = APIRouter()
router.include_router(timeline_router)
router.include_router(workers_router)
router.include_router(queue_router)
router.include_router(events_router)
router.include_router(overview_router)
router.include_router(providers_router)
router.include_router(scheduler_router)
