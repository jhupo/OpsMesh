from fastapi import APIRouter

from opsmesh.runtime.self_hosted.routes.artifacts import router as artifacts_router
from opsmesh.runtime.self_hosted.routes.enrollment import router as enrollment_router
from opsmesh.runtime.self_hosted.routes.identity import router as identity_router
from opsmesh.runtime.self_hosted.routes.jobs import router as jobs_router
from opsmesh.runtime.self_hosted.routes.mcp_jobs import router as mcp_jobs_router
from opsmesh.runtime.self_hosted.routes.worker_control import router as worker_control_router

router = APIRouter(tags=["self-hosted-runtime"])
router.include_router(enrollment_router)
router.include_router(identity_router)
router.include_router(worker_control_router)
router.include_router(jobs_router)
router.include_router(mcp_jobs_router)
router.include_router(artifacts_router)
