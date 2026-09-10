from fastapi import APIRouter

from backend.app.api.routes.workspace_task_base_resources import router as base_router
from backend.app.api.routes.workspace_task_event_resources import router as event_router
from backend.app.api.routes.workspace_task_operation_resources import router as operation_router
from backend.app.api.routes.workspace_task_plan_resources import router as plan_router
from backend.app.api.routes.workspace_task_transfer_resources import router as transfer_router

router = APIRouter()
router.include_router(base_router)
router.include_router(plan_router)
router.include_router(event_router)
router.include_router(operation_router)
router.include_router(transfer_router)
