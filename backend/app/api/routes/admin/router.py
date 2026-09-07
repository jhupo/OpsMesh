from fastapi import APIRouter, Depends

from backend.app.api.routes.admin.leases import router as leases_router
from backend.app.api.routes.admin.overview import router as overview_router
from backend.app.api.routes.admin.policies import router as policies_router
from backend.app.api.routes.admin.queues import router as queues_router
from backend.app.api.routes.admin.runtime_spaces import router as runtime_spaces_router
from backend.app.api.routes.admin.runtimes import router as runtimes_router
from backend.app.api.routes.admin.security_events import router as security_events_router
from backend.app.api.routes.admin.system import router as system_router
from backend.app.api.routes.admin.users import router as users_router
from backend.app.api.routes.admin.workers import router as workers_router
from backend.app.auth.admin import require_platform_admin

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
router.include_router(users_router)
