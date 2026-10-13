from __future__ import annotations

import logging
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import cast

from opentelemetry import _logs, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Link, Status
from opentelemetry.util.types import Attributes, AttributeValue
from sqlalchemy import Engine
from temporalio.contrib.opentelemetry import ReplaySafeTracerProvider, create_tracer_provider

from opsmesh.shared.config import Settings
from opsmesh.shared.security.redaction import is_sensitive_payload_key, redact_sensitive_text
from opsmesh.shared.telemetry.logging import RequestContextFilter, json_log_formatter

_instrument_lock = Lock()
_global_provider: ReplaySafeTracerProvider | None = None
_global_logger_provider: LoggerProvider | None = None
_global_log_handler: LoggingHandler | None = None
_dependencies_instrumented = False
_instrumented_engines: set[int] = set()


class RedactingSpanExporter(SpanExporter):
    """Apply product confidentiality policy at the native exporter boundary."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export(
            [
                ReadableSpan(
                    name=redact_sensitive_text(span.name)[:1_024],
                    context=span.context,
                    parent=span.parent,
                    resource=span.resource,
                    attributes=_safe_span_attributes(span.attributes),
                    events=[
                        Event(
                            redact_sensitive_text(event.name)[:1_024],
                            _safe_span_attributes(event.attributes),
                            event.timestamp,
                        )
                        for event in span.events
                    ],
                    links=[
                        Link(link.context, _safe_span_attributes(link.attributes))
                        for link in span.links
                    ],
                    kind=span.kind,
                    status=Status(span.status.status_code),
                    start_time=span.start_time,
                    end_time=span.end_time,
                    instrumentation_scope=span.instrumentation_scope,
                )
                for span in spans
            ]
        )

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._exporter.force_flush(timeout_millis)


def _safe_span_attributes(attributes: Attributes) -> dict[str, AttributeValue]:
    safe: dict[str, AttributeValue] = {}
    for key, value in (attributes or {}).items():
        # Native instrumentation can enrich tool schemas independently of RunConfig.
        # Keep call identity, timings and numeric usage; omit prompts and full URLs.
        if (
            key.startswith(
                (
                    "input.",
                    "output.",
                    "llm.input_messages",
                    "llm.output_messages",
                    "llm.prompts",
                    "llm.choices",
                    "llm.tools",
                    "llm.invocation_parameters",
                    "tool.parameters",
                    "tool.description",
                    "exception.message",
                    "exception.stacktrace",
                    "http.url",
                    "url.full",
                )
            )
            or is_sensitive_payload_key(key.replace(".", "_"))
            and not isinstance(value, int | float | bool)
        ):
            continue
        if isinstance(value, str):
            safe[key] = redact_sensitive_text(value)[:1_024]
        elif isinstance(value, Sequence) and all(isinstance(item, str) for item in value):
            safe[key] = [redact_sensitive_text(item)[:1_024] for item in cast(Sequence[str], value)]
        else:
            safe[key] = value
    return safe


@dataclass(frozen=True)
class TelemetryRuntime:
    enabled: bool
    provider: ReplaySafeTracerProvider
    logger_provider: LoggerProvider | None = None
    log_handler: LoggingHandler | None = None

    def shutdown(self) -> None:
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
        if self.logger_provider is not None:
            self.logger_provider.force_flush(timeout_millis=10_000)
            self.logger_provider.shutdown()
        self.provider.force_flush(timeout_millis=10_000)
        self.provider.shutdown()


def configure_tracer_provider(settings: Settings) -> ReplaySafeTracerProvider:
    """Keep native context propagation active independently of telemetry export."""
    global _global_provider

    with _instrument_lock:
        if _global_provider is None:
            existing = trace.get_tracer_provider()
            if isinstance(existing, ReplaySafeTracerProvider):
                _global_provider = existing
            else:
                export_enabled = (
                    settings.tracing_enabled and settings.otel_exporter_otlp_endpoint is not None
                )
                provider = create_tracer_provider(
                    resource=_telemetry_resource(settings),
                    sampler=ParentBased(
                        TraceIdRatioBased(settings.otel_trace_sample_ratio if export_enabled else 0)
                    ),
                )
                if export_enabled:
                    exporter = OTLPSpanExporter(
                        endpoint=settings.otel_exporter_otlp_endpoint,
                        headers=settings.otel_exporter_otlp_headers,
                        insecure=settings.otel_exporter_otlp_insecure,
                        timeout=settings.otel_export_timeout_seconds,
                    )
                    provider.add_span_processor(
                        BatchSpanProcessor(
                            RedactingSpanExporter(exporter),
                            max_queue_size=settings.otel_batch_max_queue_size,
                            max_export_batch_size=settings.otel_batch_max_export_size,
                            schedule_delay_millis=settings.otel_batch_schedule_delay_ms,
                            export_timeout_millis=settings.otel_export_timeout_seconds * 1_000,
                        )
                    )
                trace.set_tracer_provider(provider)
                _global_provider = provider
        return _global_provider


def _telemetry_resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "service.instance.id": socket.gethostname(),
            "service.version": "0.1.0",
            "deployment.environment.name": settings.environment,
        }
    )


def create_telemetry_runtime(settings: Settings, *, engine: Engine) -> TelemetryRuntime:
    global _dependencies_instrumented, _global_log_handler, _global_logger_provider

    trace_provider = configure_tracer_provider(settings)
    with _instrument_lock:
        engine_id = id(engine)
        if engine_id not in _instrumented_engines:
            SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=trace_provider)
            _instrumented_engines.add(engine_id)
        if not _dependencies_instrumented:
            RedisInstrumentor().instrument(tracer_provider=trace_provider)
            HTTPXClientInstrumentor().instrument(tracer_provider=trace_provider)
            _dependencies_instrumented = True

        if (
            settings.otel_logs_enabled
            and settings.otel_exporter_otlp_endpoint is not None
            and _global_logger_provider is None
        ):
            created_logger_provider = LoggerProvider(resource=_telemetry_resource(settings))
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
            _global_logger_provider = created_logger_provider
            _global_log_handler = created_log_handler

        if (
            _global_log_handler is not None
            and _global_log_handler not in logging.getLogger().handlers
        ):
            logging.getLogger().addHandler(_global_log_handler)
        active_logger_provider = _global_logger_provider
        active_log_handler = _global_log_handler
    return TelemetryRuntime(
        enabled=settings.otel_exporter_otlp_endpoint is not None
        and (settings.tracing_enabled or settings.otel_logs_enabled),
        provider=trace_provider,
        logger_provider=active_logger_provider,
        log_handler=active_log_handler,
    )
