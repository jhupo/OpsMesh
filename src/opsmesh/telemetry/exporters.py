"""Redact confidential attributes at the native OpenTelemetry exporter boundary."""

from collections.abc import Callable, Sequence
from typing import Protocol

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Link, Status
from opentelemetry.util.types import Attributes, AttributeValue

from opsmesh.shared.security.redaction import redact_sensitive_text


class SpanProcessorTarget(Protocol):
    """Native providers that accept processors, including SDK provider decorators."""

    def add_span_processor(self, span_processor: SpanProcessor) -> None: ...


def configure_trace_export(
    provider: SpanProcessorTarget,
    *,
    endpoint: str,
    headers: dict[str, str],
    insecure: bool,
    timeout_seconds: int,
    max_queue_size: int,
    max_export_batch_size: int,
    schedule_delay_ms: int,
    attribute_filter: Callable[[Attributes], dict[str, AttributeValue]],
) -> None:
    exporter = OTLPSpanExporter(
        endpoint=endpoint, headers=headers, insecure=insecure, timeout=timeout_seconds
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            RedactingSpanExporter(exporter, attribute_filter=attribute_filter),
            max_queue_size=max_queue_size,
            max_export_batch_size=max_export_batch_size,
            schedule_delay_millis=schedule_delay_ms,
            export_timeout_millis=timeout_seconds * 1_000,
        )
    )


def configure_log_export(
    provider: LoggerProvider,
    *,
    endpoint: str,
    headers: dict[str, str],
    insecure: bool,
    timeout_seconds: int,
    max_queue_size: int,
    max_export_batch_size: int,
    schedule_delay_ms: int,
) -> None:
    exporter = OTLPLogExporter(
        endpoint=endpoint, headers=headers, insecure=insecure, timeout=timeout_seconds
    )
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            exporter,
            max_queue_size=max_queue_size,
            max_export_batch_size=max_export_batch_size,
            schedule_delay_millis=schedule_delay_ms,
            export_timeout_millis=timeout_seconds * 1_000,
        )
    )


class RedactingSpanExporter(SpanExporter):
    """Remove confidential telemetry attributes at the native exporter boundary."""

    def __init__(
        self,
        exporter: SpanExporter,
        *,
        attribute_filter: Callable[[Attributes], dict[str, AttributeValue]],
    ) -> None:
        self._exporter = exporter
        self._attribute_filter = attribute_filter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export(
            [
                ReadableSpan(
                    name=redact_sensitive_text(span.name)[:1_024],
                    context=span.context,
                    parent=span.parent,
                    resource=span.resource,
                    attributes=self._attribute_filter(span.attributes),
                    events=[
                        Event(
                            redact_sensitive_text(event.name)[:1_024],
                            self._attribute_filter(event.attributes),
                            event.timestamp,
                        )
                        for event in span.events
                    ],
                    links=[
                        Link(link.context, self._attribute_filter(link.attributes))
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
