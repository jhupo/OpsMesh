from fastapi import APIRouter

from backend.app.api.routes.operations.events import router as events_router
from backend.app.api.routes.operations.overview import router as overview_router
from backend.app.api.routes.operations.providers import router as providers_router
from backend.app.api.routes.operations.queue import router as queue_router
from backend.app.api.routes.operations.scheduler import router as scheduler_router
from backend.app.api.routes.operations.timeline import router as timeline_router
from backend.app.api.routes.operations.workers import router as workers_router

router = APIRouter()
router.include_router(timeline_router)
router.include_router(workers_router)
router.include_router(queue_router)
router.include_router(events_router)
router.include_router(overview_router)
router.include_router(providers_router)
router.include_router(scheduler_router)
