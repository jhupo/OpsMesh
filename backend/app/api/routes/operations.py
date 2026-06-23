from fastapi import APIRouter

from backend.app.api.routes.operations_events import router as events_router
from backend.app.api.routes.operations_overview_routes import router as overview_router
from backend.app.api.routes.operations_queue import router as queue_router
from backend.app.api.routes.operations_scheduler_routes import router as scheduler_router
from backend.app.api.routes.operations_timeline import router as timeline_router
from backend.app.api.routes.operations_workers import router as workers_router

router = APIRouter()
router.include_router(timeline_router)
router.include_router(workers_router)
router.include_router(queue_router)
router.include_router(events_router)
router.include_router(overview_router)
router.include_router(scheduler_router)
