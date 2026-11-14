"""Record bounded, content-free diagnostics from native OTLP export results."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)


class ExportDiagnostics:
    """Delegate export while retaining only aggregate delivery outcomes."""

    def __init__(self, exporter: SpanExporter) -> None:
        """Borrow the native exporter owned by its span processor."""
        self._exporter = exporter
        self._lock = Lock()
        self._last_result: str | None = None
        self._last_export_at: str | None = None
        self._last_error: str | None = None
        self._successful_batches = 0
        self._failed_batches = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Observe actual exporter completion, never span enqueueing."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            result = self._exporter.export(spans)
        except Exception:
            self._record("failure", "export_exception")
            logger.warning("Langfuse span export failed")
            return SpanExportResult.FAILURE
        if result is SpanExportResult.SUCCESS:
            self._record("success", None)
        else:
            self._record("failure", "export_failed")
        return result

    def _record(self, result: str, error: str | None) -> None:
        with self._lock:
            self._last_result = result
            self._last_error = error
            self._last_export_at = datetime.now(UTC).isoformat()
            if result == "success":
                self._successful_batches += 1
            else:
                self._failed_batches += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a detached summary without span data or exception text."""
        with self._lock:
            return {
                "last_result": self._last_result,
                "successful_batches": self._successful_batches,
                "failed_batches": self._failed_batches,
                "last_export_at": self._last_export_at,
                "last_error": self._last_error,
            }

    def shutdown(self) -> None:
        """Let the owning processor close its native exporter."""
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Preserve the native exporter flush contract."""
        return self._exporter.force_flush(timeout_millis=timeout_millis)
