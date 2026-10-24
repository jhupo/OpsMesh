"""Configure native instrumentation once per instrumented process or object."""

from threading import Lock
from typing import Any, cast

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.trace import TracerProvider

_lock = Lock()


def instrument_fastapi(app: object, *, provider: TracerProvider) -> None:
    """Instrument a caller-owned ASGI app using the SDK's native adapter."""
    with _lock:
        FastAPIInstrumentor.instrument_app(
            cast(Any, app), tracer_provider=provider, excluded_urls=""
        )


def instrument_sqlalchemy(*, provider: TracerProvider) -> None:
    with _lock:
        instrumentor = SQLAlchemyInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(tracer_provider=provider)


def instrument_httpx(*, provider: TracerProvider) -> None:
    with _lock:
        instrumentor = HTTPXClientInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(tracer_provider=provider)


def instrument_redis(*, provider: TracerProvider) -> None:
    with _lock:
        instrumentor = RedisInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(tracer_provider=provider)
