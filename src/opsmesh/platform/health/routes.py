"""Low-cost process health and administrator-visible build identity."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.platform.health.schemas import HealthView, VersionView
from opsmesh.shared.version import package_version

router = APIRouter(tags=["platform"])


@router.get("/health", response_model=HealthView, operation_id="platform.health.live")
def live() -> HealthView:
    """Liveness never touches a dependency and is safe for process probes."""
    return HealthView(status="ok")


@router.get("/health/ready", response_model=HealthView, operation_id="platform.health.ready")
async def ready(session: DbSession, request: Request, response: Response) -> HealthView:
    """Readiness verifies DB, Redis and the native Temporal service."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthView(status="database_unavailable")
    if getattr(request.app.state, "redis_client", None) is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthView(status="redis_unavailable")
    try:
        request.app.state.redis_client.ping()
    except RedisError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthView(status="redis_unavailable")
    probe = getattr(request.app.state, "temporal_health_probe", None)
    if probe is None or not await probe():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthView(status="temporal_unavailable")
    return HealthView(status="ready")


@router.get(
    "/platform/system/version", response_model=VersionView, operation_id="platform.system.version"
)
def version(request: Request, user: CurrentUserDep) -> VersionView:
    require_platform_admin(user)
    settings = getattr(request.app.state, "settings", None)
    return VersionView(
        service=getattr(settings, "service_name", "opsmesh-backend"),
        version=package_version(),
        environment=getattr(settings, "environment", "unknown"),
        build=getattr(settings, "build_commit", None),
    )
