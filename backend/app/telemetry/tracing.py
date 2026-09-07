from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from threading import Lock

from fastapi import FastAPI
from opentelemetry import _logs, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy import Engine

from backend.app.core.config import Settings
from backend.app.core.logging import RequestContextFilter, json_log_formatter

_instrument_lock = Lock()
_global_provider: TracerProvider | None = None
_global_logger_provider: LoggerProvider | None = None
_global_log_handler: LoggingHandler | None = None
_dependencies_instrumented = False
_instrumented_engines: set[int] = set()


@dataclass(frozen=True)
class TelemetryRuntime:
    enabled: bool
    provider: TracerProvider | None = None
    logger_provider: LoggerProvider | None = None
    log_handler: LoggingHandler | None = None

    def shutdown(self) -> None:
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
        if self.logger_provider is not None:
            self.logger_provider.force_flush(timeout_millis=10_000)
            self.logger_provider.shutdown()
        if self.provider is not None:
            self.provider.force_flush(timeout_millis=10_000)
            self.provider.shutdown()


def configure_api_telemetry(
    app: FastAPI,
    settings: Settings,
    *,
    engine: Engine,
) -> TelemetryRuntime:
    runtime = _configure_provider(settings, engine=engine)
    if runtime.provider is None:
        return runtime
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=runtime.provider,
        excluded_urls=settings.otel_excluded_urls,
    )
    return runtime


def configure_worker_telemetry(settings: Settings, *, engine: Engine) -> TelemetryRuntime:
    return _configure_provider(settings, engine=engine)


def _configure_provider(settings: Settings, *, engine: Engine) -> TelemetryRuntime:
    global _dependencies_instrumented, _global_log_handler
    global _global_logger_provider, _global_provider

    if settings.otel_exporter_otlp_endpoint is None or not (
        settings.tracing_enabled or settings.otel_logs_enabled
    ):
        return TelemetryRuntime(enabled=False)

    with _instrument_lock:
        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.instance.id": socket.gethostname(),
                "service.version": "0.1.0",
                "deployment.environment.name": settings.environment,
            }
        )
        if settings.tracing_enabled and _global_provider is None:
            created_provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(
                    TraceIdRatioBased(settings.otel_trace_sample_ratio)
                ),
            )
            exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                headers=settings.otel_exporter_otlp_headers,
                insecure=settings.otel_exporter_otlp_insecure,
                timeout=settings.otel_export_timeout_seconds,
            )
            created_provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    max_queue_size=settings.otel_batch_max_queue_size,
                    max_export_batch_size=settings.otel_batch_max_export_size,
                    schedule_delay_millis=settings.otel_batch_schedule_delay_ms,
                    export_timeout_millis=settings.otel_export_timeout_seconds * 1_000,
                )
            )
            trace.set_tracer_provider(created_provider)
            _global_provider = created_provider

        trace_provider = _global_provider
        if trace_provider is not None:
            engine_id = id(engine)
            if engine_id not in _instrumented_engines:
                SQLAlchemyInstrumentor().instrument(
                    engine=engine,
                    tracer_provider=trace_provider,
                )
                _instrumented_engines.add(engine_id)
            if not _dependencies_instrumented:
                RedisInstrumentor().instrument(tracer_provider=trace_provider)
                HTTPXClientInstrumentor().instrument(tracer_provider=trace_provider)
                _dependencies_instrumented = True

        if settings.otel_logs_enabled and _global_logger_provider is None:
            created_logger_provider = LoggerProvider(resource=resource)
            log_exporter = OTLPLogExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                headers=settings.otel_exporter_otlp_headers,
                insecure=settings.otel_exporter_otlp_insecure,
                timeout=settings.otel_export_timeout_seconds,
            )
            created_logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    log_exporter,
                    max_queue_size=settings.otel_batch_max_queue_size,
                    max_export_batch_size=settings.otel_batch_max_export_size,
                    schedule_delay_millis=settings.otel_batch_schedule_delay_ms,
                    export_timeout_millis=settings.otel_export_timeout_seconds * 1_000,
                )
            )
            _logs.set_logger_provider(created_logger_provider)
            created_log_handler = LoggingHandler(
                level=logging.NOTSET,
                logger_provider=created_logger_provider,
            )
            created_log_handler.addFilter(
                RequestContextFilter(
                    service_name=settings.service_name,
                    environment=settings.environment,
                )
            )
            created_log_handler.setFormatter(json_log_formatter())
            logging.getLogger().addHandler(created_log_handler)
            _global_logger_provider = created_logger_provider
            _global_log_handler = created_log_handler

        active_logger_provider = _global_logger_provider
        active_log_handler = _global_log_handler
    return TelemetryRuntime(
        enabled=True,
        provider=trace_provider,
        logger_provider=active_logger_provider,
        log_handler=active_log_handler,
    )
