from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse

from backend.app.core.common.config import Settings, get_settings
from backend.app.core.common.metrics import metrics_registry
from backend.app.core.db.session import get_db_session
from backend.app.core.redis.dependencies import RedisClient, get_redis_client
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.runtime.operations.metrics.prometheus import OperationsPrometheusMetricsService

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    gauges = []
    try:
        gauges = OperationsPrometheusMetricsService(
            session,
            redis,
            RedisKeyBuilder(settings.redis_key_prefix),
        ).prometheus_gauges(
            settings.worker_queue_name,
            audit_integrity_stale_after_seconds=(
                settings.audit_integrity_stale_after_seconds
            ),
        )
    except (OSError, RedisError, SQLAlchemyError, TimeoutError):
        gauges = []
    return PlainTextResponse(
        metrics_registry.render_prometheus(gauges=gauges),
        media_type=CONTENT_TYPE_LATEST,
    )
