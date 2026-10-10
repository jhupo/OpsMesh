from fastapi import APIRouter

from opsmesh.agents.providers.operation_routes import router as providers_router
from opsmesh.orchestration.scheduling.operation_routes import router as scheduler_router
from opsmesh.runtime.operations.routes.events import router as events_router
from opsmesh.runtime.operations.routes.overview import router as overview_router
from opsmesh.runtime.operations.routes.timeline import router as timeline_router
from opsmesh.runtime.queues.routes import router as queue_router
from opsmesh.runtime.workers.routes import router as workers_router

router = APIRouter()
router.include_router(timeline_router)
router.include_router(workers_router)
router.include_router(queue_router)
router.include_router(events_router)
router.include_router(overview_router)
router.include_router(providers_router)
router.include_router(scheduler_router)
