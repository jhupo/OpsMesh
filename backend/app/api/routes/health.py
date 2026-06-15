from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette import status

from backend.app.core.config import Settings
from backend.app.db.session import get_db_session
from backend.app.files.storage import create_storage
from backend.app.operations.models import WorkerNode
from backend.app.operations.utils import ensure_aware_utc
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter()


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    service: str = Field(examples=["opsmesh-backend"])
    environment: str = Field(examples=["local"])
    request_id: str | None = Field(default=None, examples=["01J8G7Y4R7D6C6K4A1RSPJ7J7P"])


class ReadinessResponse(HealthResponse):
    dependencies: dict[str, str]


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    return _health_response(request)


@router.get("/health/live", response_model=HealthResponse)
async def liveness_check(request: Request) -> HealthResponse:
    return _health_response(request)


@router.get("/health/startup", response_model=ReadinessResponse)
def startup_check(request: Request) -> ReadinessResponse:
    settings: Settings = request.app.state.settings
    return ReadinessResponse(
        **_health_response(request).model_dump(),
        dependencies={
            "configuration": "ok",
            "storage": _check_storage(settings),
        },
    )


def _health_response(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.environment,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/health/ready", response_model=ReadinessResponse)
def readiness_check(
    request: Request,
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
) -> ReadinessResponse:
    settings: Settings = request.app.state.settings
    dependencies = {
        "database": _check_database(session),
        "redis": _check_redis(redis),
        "storage": _check_storage(settings),
        "worker_queue": _check_worker_queue(redis, settings),
        "workers_online": _check_workers_online(session, settings),
    }
    if any(_dependency_failed(value) for value in dependencies.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Service dependencies are not ready",
                "dependencies": dependencies,
            },
        )

    return ReadinessResponse(
        **_health_response(request).model_dump(),
        dependencies=dependencies,
    )


def _check_database(session: Session) -> str:
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return "unavailable"
    return "ok"


def _check_redis(redis: RedisClient) -> str:
    try:
        redis.ping()
    except Exception:
        return "unavailable"
    return "ok"


def _check_storage(settings: Settings) -> str:
    try:
        storage = create_storage(settings)
        probe = ".healthcheck"
        storage.write(probe, b"ok")
        storage.delete(probe)
    except Exception:
        return "unavailable"
    return "ok"


def _check_worker_queue(redis: RedisClient, settings: Settings) -> str:
    try:
        queue_key = RedisKeyBuilder(settings.redis_key_prefix).queue(settings.worker_queue_name)
        redis.llen(queue_key)
    except Exception:
        return "unavailable"
    return "ok"


def _check_workers_online(session: Session, settings: Settings) -> str:
    if not settings.readiness_worker_check_enabled:
        return "disabled"
    try:
        stale_before = datetime.now(UTC) - timedelta(
            seconds=settings.readiness_worker_stale_after_seconds
        )
        workers = session.scalars(
            select(WorkerNode).where(
                WorkerNode.queue_name == settings.worker_queue_name,
                WorkerNode.status == "online",
                WorkerNode.drain_requested_at.is_(None),
            )
        ).all()
    except Exception:
        return "unavailable"
    if not workers:
        return "none_online"
    if any(ensure_aware_utc(worker.last_seen_at) >= stale_before for worker in workers):
        return "ok"
    return "stale"


def _dependency_failed(value: str) -> bool:
    return value not in {"ok", "disabled"}
