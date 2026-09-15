from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import Engine

from backend.app.core.config import Settings
from backend.app.observability.telemetry.tracing import (
    TelemetryRuntime,
    create_telemetry_runtime,
)


def configure_api_telemetry(
    app: FastAPI,
    settings: Settings,
    *,
    engine: Engine,
) -> TelemetryRuntime:
    runtime = create_telemetry_runtime(settings, engine=engine)
    if runtime.provider is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=runtime.provider,
            excluded_urls=settings.otel_excluded_urls,
        )
    return runtime


def configure_worker_telemetry(
    settings: Settings,
    *,
    engine: Engine,
) -> TelemetryRuntime:
    return create_telemetry_runtime(settings, engine=engine)
