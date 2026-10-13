from fastapi import APIRouter

from opsmesh.orchestration.tasks.routes.base import router as base_router
from opsmesh.orchestration.tasks.routes.events import router as event_router
from opsmesh.orchestration.tasks.routes.operations import router as operations_router
from opsmesh.orchestration.tasks.routes.transfers import router as transfers_router

router = APIRouter()
router.include_router(base_router)
router.include_router(event_router)
router.include_router(operations_router)
router.include_router(transfers_router)
