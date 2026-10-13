from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette import status
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client

from opsmesh.resources.storage.backends import create_storage
from opsmesh.runtime.temporal.client import connect_temporal
from opsmesh.shared.config import Settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import get_redis_client

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
def health_check(request: Request) -> HealthResponse:
    return _health_response(request)


@router.get("/health/live", response_model=HealthResponse)
def liveness_check(request: Request) -> HealthResponse:
    return _health_response(request)


@router.get("/health/startup", response_model=ReadinessResponse)
def startup_check(request: Request) -> ReadinessResponse:
    settings: Settings = request.app.state.settings
    return ReadinessResponse(
        **_health_response(request).model_dump(),
        dependencies={
            "configuration": "ok",
            "storage": _check_storage(settings),
            "temporal": _check_temporal_configuration(settings),
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
async def readiness_check(
    request: Request,
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
) -> ReadinessResponse:
    settings: Settings = request.app.state.settings
    database, redis_status, storage, temporal = await asyncio.gather(
        asyncio.to_thread(_check_database, session),
        asyncio.to_thread(_check_redis, redis),
        asyncio.to_thread(_check_storage, settings),
        _check_temporal(request),
    )
    dependencies = {
        "database": database,
        "redis": redis_status,
        "storage": storage,
        "temporal": temporal,
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
        with closing(create_storage(settings)) as storage:
            probe = ".healthcheck"
            storage.write(probe, b"ok")
            storage.delete(probe)
    except Exception:
        return "unavailable"
    return "ok"


def _check_temporal_configuration(settings: Settings) -> str:
    if not settings.temporal_address.strip():
        return "unavailable"
    if not settings.temporal_namespace.strip() or not settings.temporal_task_queue.strip():
        return "unavailable"
    return "ok"


async def _check_temporal(request: Request) -> str:
    settings: Settings = request.app.state.settings
    try:
        async with asyncio.timeout(5):
            async with request.app.state.temporal_client_lock:
                client: Client | None = request.app.state.temporal_client
                if client is None:
                    client = await connect_temporal(settings)
                    request.app.state.temporal_client = client
            await client.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace=settings.temporal_namespace),
                timeout=timedelta(seconds=4),
            )
    except Exception:
        return "unavailable"
    return "ok"


def _dependency_failed(value: str) -> bool:
    return value not in {"ok", "disabled"}
