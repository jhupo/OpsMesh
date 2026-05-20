from fastapi import APIRouter
from starlette.responses import PlainTextResponse

from backend.app.core.metrics import metrics_registry

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        metrics_registry.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
