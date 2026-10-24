"""Compose process telemetry; native SDK objects own tracing and log resources."""

import atexit
import logging
import socket
from collections.abc import Sequence
from threading import Lock
from typing import cast

from fastapi import FastAPI
from opentelemetry import _logs, trace
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.util.types import Attributes, AttributeValue
from temporalio.contrib.opentelemetry import ReplaySafeTracerProvider, create_tracer_provider

from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.config import Settings
from opsmesh.shared.security.redaction import is_sensitive_payload_key, redact_sensitive_text
from opsmesh.shared.version import package_version
from opsmesh.telemetry.exporters import configure_log_export, configure_trace_export
from opsmesh.telemetry.instrumentation import (
    instrument_fastapi,
    instrument_httpx,
    instrument_redis,
    instrument_sqlalchemy,
)
from opsmesh.telemetry.logging import RequestContextFilter, json_log_formatter, shutdown_logging

_lock = Lock()
_provider: ReplaySafeTracerProvider | None = None
_owns_provider = False
_logger_provider: LoggerProvider | None = None
_log_handler: LoggingHandler | None = None
_shutdown = False

# Native instrumentation can enrich tool schemas independently of RunConfig.
# Keep call identity, timings and numeric usage; omit prompts and full URLs.
_SUPPRESSED_ATTRIBUTE_PREFIXES = (
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


def filter_span_attributes(attributes: Attributes) -> dict[str, AttributeValue]:
    safe: dict[str, AttributeValue] = {}
    for key, value in (attributes or {}).items():
        if key.startswith(_SUPPRESSED_ATTRIBUTE_PREFIXES):
            continue
        if is_sensitive_payload_key(key.replace(".", "_"), rules=PLATFORM_REDACTION) and not (
            key.startswith("llm.token_count.")
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        ):
            continue
        if isinstance(value, str):
            safe[key] = redact_sensitive_text(value, rules=PLATFORM_REDACTION)[:1_024]
        elif isinstance(value, Sequence) and all(isinstance(item, str) for item in value):
            safe[key] = [
                redact_sensitive_text(item, rules=PLATFORM_REDACTION)[:1_024]
                for item in cast(Sequence[str], value)
            ]
        else:
            safe[key] = value
    return safe


def configure_api_telemetry(app: FastAPI, settings: Settings) -> ReplaySafeTracerProvider:
    provider = configure_process_telemetry(settings)
    instrument_fastapi(app, provider=provider)
    return provider


def configure_process_telemetry(settings: Settings) -> ReplaySafeTracerProvider:
    """Initialize native providers and instrumentation before creating connection pools."""
    provider = _configure_process_telemetry(settings)
    instrument_sqlalchemy(provider=provider)
    instrument_redis(provider=provider)
    instrument_httpx(provider=provider)
    return provider


def _configure_process_telemetry(settings: Settings) -> ReplaySafeTracerProvider:
    global _provider, _owns_provider, _logger_provider, _log_handler

    with _lock:
        if _shutdown:
            raise RuntimeError("Process telemetry has already shut down")
        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.instance.id": socket.gethostname(),
                "service.version": package_version(),
                "deployment.environment.name": settings.environment,
            }
        )
        if _provider is None:
            existing = trace.get_tracer_provider()
            if isinstance(existing, ReplaySafeTracerProvider):
                _provider = existing
            elif not isinstance(existing, trace.ProxyTracerProvider):
                raise RuntimeError("Temporal requires a native replay-safe tracer provider")
            else:
                endpoint = settings.otel_exporter_otlp_endpoint
                export_enabled = settings.tracing_enabled and endpoint is not None
                provider = create_tracer_provider(
                    resource=resource,
                    sampler=ParentBased(
                        TraceIdRatioBased(settings.otel_trace_sample_ratio if export_enabled else 0)
                    ),
                    shutdown_on_exit=False,
                )
                try:
                    if export_enabled and endpoint is not None:
                        configure_trace_export(
                            provider,
                            endpoint=endpoint,
                            attribute_filter=filter_span_attributes,
                            headers=settings.otel_exporter_otlp_headers,
                            insecure=settings.otel_exporter_otlp_insecure,
                            timeout_seconds=settings.otel_export_timeout_seconds,
                            max_queue_size=settings.otel_batch_max_queue_size,
                            max_export_batch_size=settings.otel_batch_max_export_size,
                            schedule_delay_ms=settings.otel_batch_schedule_delay_ms,
                            redaction=PLATFORM_REDACTION,
                        )
                except Exception:
                    provider.shutdown()
                    raise
                trace.set_tracer_provider(provider)
                _provider = provider
                _owns_provider = True
        if (
            settings.otel_logs_enabled
            and settings.otel_exporter_otlp_endpoint is not None
            and _logger_provider is None
        ):
            logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
            try:
                configure_log_export(
                    logger_provider,
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    headers=settings.otel_exporter_otlp_headers,
                    insecure=settings.otel_exporter_otlp_insecure,
                    timeout_seconds=settings.otel_export_timeout_seconds,
                    max_queue_size=settings.otel_batch_max_queue_size,
                    max_export_batch_size=settings.otel_batch_max_export_size,
                    schedule_delay_ms=settings.otel_batch_schedule_delay_ms,
                )
            except Exception:
                logger_provider.shutdown()
                raise
            _logs.set_logger_provider(logger_provider)
            if _logs.get_logger_provider() is not logger_provider:
                logger_provider.shutdown()
                raise RuntimeError("Process log export already has a configured provider")
            handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
            handler.addFilter(
                RequestContextFilter(
                    service_name=settings.service_name,
                    environment=settings.environment,
                    redaction=PLATFORM_REDACTION,
                )
            )
            handler.setFormatter(json_log_formatter())
            _logger_provider = logger_provider
            _log_handler = handler
        if _log_handler is not None and _log_handler not in logging.getLogger().handlers:
            logging.getLogger().addHandler(_log_handler)
        return _provider


def flush_process_telemetry() -> None:
    with _lock:
        if _shutdown:
            return
        if _logger_provider is not None:
            _logger_provider.force_flush(timeout_millis=10_000)
        if _provider is not None:
            _provider.force_flush(timeout_millis=10_000)


def shutdown_process_telemetry() -> None:
    global _shutdown

    with _lock:
        if _shutdown:
            return
        _shutdown = True
        shutdown_logging()
        if _log_handler is not None:
            logging.getLogger().removeHandler(_log_handler)
        if _logger_provider is not None:
            _logger_provider.shutdown()
        if _provider is not None and _owns_provider:
            _provider.shutdown()


atexit.register(shutdown_process_telemetry)
