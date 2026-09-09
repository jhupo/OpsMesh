from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.releases.service import ReleaseUpdateService
from backend.app.api.routes.admin.dependencies import RedisClient
from backend.app.api.schemas.admin import (
    AdminReleaseUpdateCheckResponse,
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
        raise HTTPException(status_code=502, detail="Update check failed") from exc
    return AdminReleaseUpdateCheckResponse(
        current=AdminReleaseVersionResponse(**result.current.__dict__),
        latest=AdminReleaseVersionResponse(**result.latest.__dict__) if result.latest else None,
        update_available=result.update_available,
        release_url=result.release_url,
        assets=[item.__dict__ for item in result.assets],
        cached=result.cached,
    )
