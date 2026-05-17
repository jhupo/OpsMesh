from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette import status

from backend.app.core.config import Settings
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client

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


class DependencyHealth(BaseModel):
    database: Literal["ok"]
    redis: Literal["ok"]


class ReadinessResponse(HealthResponse):
    dependencies: DependencyHealth


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
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
    try:
        session.execute(text("SELECT 1"))
        redis.ping()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service dependencies are not ready",
        ) from exc

    return ReadinessResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.environment,
        request_id=getattr(request.state, "request_id", None),
        dependencies=DependencyHealth(database="ok", redis="ok"),
    )
