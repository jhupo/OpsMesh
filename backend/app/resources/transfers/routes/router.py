from fastapi import APIRouter

from backend.app.resources.lifecycle.routes import router as lifecycle_router
from backend.app.resources.transfers.routes.archive import router as archive_router
from backend.app.resources.transfers.routes.archive_import import router as archive_import_router
from backend.app.resources.transfers.routes.archive_jobs import router as archive_job_router
from backend.app.resources.transfers.routes.metadata import router as metadata_router

router = APIRouter(prefix="/workspaces/{workspace_id}/exports", tags=["exports"])
router.include_router(lifecycle_router)
router.include_router(metadata_router)
router.include_router(archive_router)
router.include_router(archive_job_router)
router.include_router(archive_import_router)
