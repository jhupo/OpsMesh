from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse

from opsmesh.runtime.operations.metrics.prometheus import OperationsMetricsCollector
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.telemetry.metrics import metrics_registry

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    gauges = []
    try:
        gauges = OperationsMetricsCollector(session).prometheus_gauges(
            audit_integrity_stale_after_seconds=(settings.audit_integrity_stale_after_seconds),
        )
    except (OSError, SQLAlchemyError, TimeoutError):
        gauges = []
    return PlainTextResponse(
        metrics_registry.render_prometheus(gauges=gauges),
        media_type=CONTENT_TYPE_LATEST,
    )
