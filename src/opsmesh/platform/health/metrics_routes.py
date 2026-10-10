from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse

from opsmesh.platform.settings.policy import operational_configuration
from opsmesh.runtime.operations.metrics.prometheus import OperationsPrometheusMetricsService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import RedisClient, get_redis_client
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.shared.telemetry.metrics import metrics_registry

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(
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
            worker_stale_after_seconds=operational_configuration(
                session
            ).worker_stale_after_seconds,
            queue_scan_limit=operational_configuration(session).queue_scan_limit,
            audit_integrity_stale_after_seconds=(settings.audit_integrity_stale_after_seconds),
        )
    except (OSError, RedisError, SQLAlchemyError, TimeoutError):
        gauges = []
    return PlainTextResponse(
        metrics_registry.render_prometheus(gauges=gauges),
        media_type=CONTENT_TYPE_LATEST,
    )
