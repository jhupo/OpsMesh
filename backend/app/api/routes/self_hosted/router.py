from fastapi import APIRouter

from backend.app.api.routes.self_hosted.artifacts import router as artifacts_router
from backend.app.api.routes.self_hosted.enrollment import router as enrollment_router
from backend.app.api.routes.self_hosted.identity import router as identity_router
from backend.app.api.routes.self_hosted.jobs import router as jobs_router
from backend.app.api.routes.self_hosted.mcp_jobs import router as mcp_jobs_router
from backend.app.api.routes.self_hosted.worker_control import router as worker_control_router

router = APIRouter(tags=["self-hosted-runtime"])
router.include_router(enrollment_router)
router.include_router(identity_router)
router.include_router(worker_control_router)
router.include_router(jobs_router)
router.include_router(mcp_jobs_router)
router.include_router(artifacts_router)
