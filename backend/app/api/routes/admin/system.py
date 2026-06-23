from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.releases.models import ReleaseUpdateStart
from backend.app.admin.releases.service import ReleaseUpdateService
from backend.app.api.routes.admin.dependencies import RedisClient
from backend.app.api.schemas.admin import (
    AdminReleaseCommandResponse,
    AdminReleaseRestartRequest,
    AdminReleaseRollbackRequest,
    AdminReleaseUpdateCheckResponse,
    AdminReleaseUpdateRequest,
    AdminReleaseVersionResponse,
    AdminSystemConfigurationResponse,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.executors import blocking_executor_snapshot
from backend.app.core.resources import recommend_runtime_resources
from backend.app.db.session import database_pool_snapshot
from backend.app.redis.client import redis_pool_snapshot
from backend.app.redis.dependencies import get_redis_client

router = APIRouter()


@router.get("/system/configuration", response_model=AdminSystemConfigurationResponse)
async def admin_system_configuration(
    settings: Settings = Depends(get_settings),
    redis: RedisClient = Depends(get_redis_client),
) -> AdminSystemConfigurationResponse:
    recommendation = recommend_runtime_resources()
    recommended_resources = recommendation.as_dict()
    configured_resources = {
        "database_pool_size": settings.database_pool_size,
        "database_max_overflow": settings.database_max_overflow,
        "blocking_thread_pool_workers": settings.blocking_thread_pool_workers,
        "redis_max_connections": settings.redis_max_connections,
    }
    resource_deltas = {
        key: configured_resources[key] - recommended_resources[key]
        for key in configured_resources
    }
    return AdminSystemConfigurationResponse(
        settings=settings.redacted_summary(),
        recommended_resources=recommended_resources,
        configured_resources=configured_resources,
        resource_deltas=resource_deltas,
        blocking_executor=blocking_executor_snapshot(settings).as_dict(),
        database_pool=database_pool_snapshot().as_dict(),
        redis_pool=redis_pool_snapshot(redis).as_dict(),
    )


@router.get("/system/version", response_model=AdminReleaseVersionResponse)
async def admin_system_version(
    settings: Settings = Depends(get_settings),
) -> AdminReleaseVersionResponse:
    return AdminReleaseVersionResponse(
        **ReleaseUpdateService(settings).current_version().__dict__
    )


@router.get("/system/check-updates", response_model=AdminReleaseUpdateCheckResponse)
async def admin_check_updates(
    force: bool = Query(default=False),
    settings: Settings = Depends(get_settings),
) -> AdminReleaseUpdateCheckResponse:
    try:
        result = ReleaseUpdateService(settings).check_updates(force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Update check failed: {exc}") from exc
    return AdminReleaseUpdateCheckResponse(
        current=AdminReleaseVersionResponse(**result.current.__dict__),
        latest=AdminReleaseVersionResponse(**result.latest.__dict__) if result.latest else None,
        update_available=result.update_available,
        release_url=result.release_url,
        assets=[item.__dict__ for item in result.assets],
        cached=result.cached,
    )


@router.post("/system/update", response_model=AdminReleaseCommandResponse)
async def admin_release_update(
    request: AdminReleaseUpdateRequest,
    settings: Settings = Depends(get_settings),
) -> AdminReleaseCommandResponse:
    try:
        result = ReleaseUpdateService(settings).update(
            request.tag,
            dry_run=request.dry_run,
            manifest_url=request.manifest_url,
            manifest_file=request.manifest_file,
            bundle_url=request.bundle_url,
            bundle_file=request.bundle_file,
            checksum_url=request.checksum_url,
            checksum_file=request.checksum_file,
            release_dir=request.release_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"Update script not found: {exc}") from exc
    return _release_command_response(result)


@router.post("/system/rollback", response_model=AdminReleaseCommandResponse)
async def admin_release_rollback(
    request: AdminReleaseRollbackRequest,
    settings: Settings = Depends(get_settings),
) -> AdminReleaseCommandResponse:
    try:
        result = ReleaseUpdateService(settings).rollback(
            dry_run=request.dry_run,
            release_dir=request.release_dir,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"Update script not found: {exc}") from exc
    return _release_command_response(result)


@router.post("/system/restart", response_model=AdminReleaseCommandResponse)
async def admin_release_restart(
    request: AdminReleaseRestartRequest,
    settings: Settings = Depends(get_settings),
) -> AdminReleaseCommandResponse:
    try:
        result = ReleaseUpdateService(settings).restart(
            dry_run=request.dry_run,
            release_dir=request.release_dir,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"Update script not found: {exc}") from exc
    return _release_command_response(result)


def _release_command_response(result: ReleaseUpdateStart) -> AdminReleaseCommandResponse:
    return AdminReleaseCommandResponse(
        action=result.action,
        tag=result.tag,
        manifest_url=result.manifest_url,
        manifest_file=result.manifest_file,
        bundle_url=result.bundle_url,
        bundle_file=result.bundle_file,
        checksum_url=result.checksum_url,
        checksum_file=result.checksum_file,
        release_dir=result.release_dir,
        command=result.command,
        dry_run=result.dry_run,
        started=result.started,
        pid=result.pid,
    )
