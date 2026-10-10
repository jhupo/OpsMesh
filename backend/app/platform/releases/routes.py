from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.platform.releases.schemas import (
    AdminReleaseUpdateCheckResponse,
    AdminReleaseVersionResponse,
)
from backend.app.platform.releases.service import ReleaseUpdateService
from backend.app.shared.config import Settings, get_settings

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/system/version", response_model=AdminReleaseVersionResponse)
def admin_system_version(
    settings: Settings = Depends(get_settings),
) -> AdminReleaseVersionResponse:
    return AdminReleaseVersionResponse(**ReleaseUpdateService(settings).current_version().__dict__)


@router.get("/system/check-updates", response_model=AdminReleaseUpdateCheckResponse)
def admin_check_updates(
    force: bool = Query(default=False),
    settings: Settings = Depends(get_settings),
) -> AdminReleaseUpdateCheckResponse:
    try:
        result = ReleaseUpdateService(settings).check_updates(force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Update check failed") from exc
    return AdminReleaseUpdateCheckResponse(
        current=AdminReleaseVersionResponse(**result.current.__dict__),
        latest=AdminReleaseVersionResponse(**result.latest.__dict__) if result.latest else None,
        update_available=result.update_available,
        release_url=result.release_url,
        assets=[item.__dict__ for item in result.assets],
        cached=result.cached,
    )
