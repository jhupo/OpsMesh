from fastapi import APIRouter, Depends

from backend.app.api.dependencies.admin import require_platform_admin
from backend.app.api.routes.platform.leases import router as leases_router
from backend.app.api.routes.platform.overview import router as overview_router
from backend.app.api.routes.platform.policies import router as policies_router
from backend.app.api.routes.platform.queues import router as queues_router
from backend.app.api.routes.platform.runtime_spaces import router as runtime_spaces_router
from backend.app.api.routes.platform.runtimes import router as runtimes_router
from backend.app.api.routes.platform.security_events import router as security_events_router
from backend.app.api.routes.platform.system import router as system_router
from backend.app.api.routes.platform.updates import router as updates_router
from backend.app.api.routes.platform.users import router as users_router
from backend.app.api.routes.platform.workers import router as workers_router

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)
router.include_router(overview_router)
router.include_router(workers_router)
router.include_router(runtime_spaces_router)
router.include_router(leases_router)
router.include_router(queues_router)
router.include_router(runtimes_router)
router.include_router(policies_router)
router.include_router(security_events_router)
router.include_router(system_router)
router.include_router(updates_router)
router.include_router(users_router)
