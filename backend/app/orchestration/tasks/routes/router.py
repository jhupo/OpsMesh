from fastapi import APIRouter

from backend.app.orchestration.planning.routes import router as plan_router
from backend.app.orchestration.tasks.routes.base import router as base_router
from backend.app.orchestration.tasks.routes.events import router as event_router
from backend.app.orchestration.tasks.routes.operations import router as operation_router
from backend.app.orchestration.tasks.routes.transfers import router as transfer_router

router = APIRouter()
router.include_router(base_router)
router.include_router(plan_router)
router.include_router(event_router)
router.include_router(operation_router)
router.include_router(transfer_router)
