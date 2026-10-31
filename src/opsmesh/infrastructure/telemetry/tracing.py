"""Application-owned OTel export, bounded queues and W3C propagation."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import Any, TextIO

logger = logging.getLogger(__name__)


class ApplicationTraceExporter:
    def __init__(
        self,
        directory: Path,
        *,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 5,
        queue_size: int = 2048,
        service_name: str = "opsmesh",
    ) -> None:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        self.timeout_seconds = timeout_seconds
        self.stream: TextIO | None = None
        self.provider: Any = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=timeout_seconds)
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    max_queue_size=queue_size,
                    max_export_batch_size=min(queue_size, 512),
                    schedule_delay_millis=500,
                    export_timeout_millis=int(timeout_seconds * 1000),
                )
            )
        else:
            directory.mkdir(parents=True, exist_ok=True)
            self.stream = (directory / "traces.jsonl").open("a", encoding="utf-8")
            self.provider.add_span_processor(
                SimpleSpanProcessor(
                    ConsoleSpanExporter(
                        out=self.stream,
                        formatter=lambda span: span.to_json(indent=None) + "\n",
                    )
                )
            )
        self.tracer = self.provider.get_tracer("opsmesh.platform")
        self._closed = False

    @contextmanager
    def span(
        self,
        name: str,
        *,
        carrier: Mapping[str, str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        context = TraceContextTextMapPropagator().extract(dict(carrier)) if carrier else None
        with self.tracer.start_as_current_span(
            name,
            context=context,
            attributes=attributes or {},
            record_exception=False,
        ) as span:
            yield span

    def close(self) -> bool:
        if self._closed:
            return True
        self._closed = True

        def shutdown() -> None:
            try:
                self.provider.force_flush(timeout_millis=int(self.timeout_seconds * 1000))
                self.provider.shutdown()
            finally:
                if self.stream:
                    self.stream.close()

        thread = Thread(target=shutdown, name="opsmesh-trace-shutdown", daemon=True)
        thread.start()
        thread.join(self.timeout_seconds + 0.25)
        if thread.is_alive():
            logger.warning("Trace export shutdown exceeded its deadline")
            return False
        return True


def current_trace_carrier() -> dict[str, str]:
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier
