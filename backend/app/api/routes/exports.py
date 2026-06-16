from fastapi import APIRouter

from backend.app.api.routes.export_archive_import_routes import router as archive_import_router
from backend.app.api.routes.export_archive_job_routes import router as archive_job_router
from backend.app.api.routes.export_archive_routes import router as archive_router
from backend.app.api.routes.export_lifecycle_routes import router as lifecycle_router
from backend.app.api.routes.export_metadata_routes import router as metadata_router

router = APIRouter(prefix="/workspaces/{workspace_id}/exports", tags=["exports"])
router.include_router(lifecycle_router)
router.include_router(metadata_router)
router.include_router(archive_router)
router.include_router(archive_job_router)
router.include_router(archive_import_router)
