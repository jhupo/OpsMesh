from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette import status

from backend.app.core.config import Settings
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter()


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    service: str = Field(examples=["chaincloud-backend"])
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
    }
    if any(value != "ok" for value in dependencies.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service dependencies are not ready",
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
        root = Path(settings.storage_root)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".healthcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
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
