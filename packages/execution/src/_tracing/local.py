"""Bounded local diagnostics for SDK traces without exporting content."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

from agents.tracing import TracingProcessor


@dataclass(frozen=True)
class SpanSummary:
    """Content-free description of one finished SDK span."""

    trace_id: str
    span_id: str
    kind: str
    has_error: bool


class LocalTraceProcessor(TracingProcessor):
    """Keep bounded trace counters and span summaries in memory."""

    def __init__(self, capacity: int = 200) -> None:
        """Create a processor retaining at most ``capacity`` summaries."""
        self._lock = threading.Lock()
        self._recent: deque[SpanSummary] = deque(maxlen=capacity)
        self._traces_started = 0
        self._traces_finished = 0
        self._spans_started = 0
        self._spans_finished = 0
        self._errors = 0
        self._closed = False

    def on_trace_start(self, trace: Any) -> None:
        """Count a started trace without retaining its metadata."""
        with self._lock:
            self._traces_started += 1

    def on_trace_end(self, trace: Any) -> None:
        """Count a finished trace."""
        with self._lock:
            self._traces_finished += 1

    def on_span_start(self, span: Any) -> None:
        """Count a started span."""
        with self._lock:
            self._spans_started += 1

    def on_span_end(self, span: Any) -> None:
        """Record a content-free span summary."""
        error = getattr(span, "error", None)
        summary = SpanSummary(
            trace_id=str(getattr(span, "trace_id", "")),
            span_id=str(getattr(span, "span_id", "")),
            kind=type(getattr(span, "span_data", None)).__name__,
            has_error=error is not None,
        )
        with self._lock:
            self._spans_finished += 1
            if summary.has_error:
                self._errors += 1
            self._recent.append(summary)

    def force_flush(self) -> None:
        """Local counters need no flushing."""

    def shutdown(self) -> None:
        """Mark the processor closed; counters remain queryable."""
        with self._lock:
            self._closed = True

    def snapshot(self) -> dict[str, Any]:
        """Return counters and the bounded recent span summaries."""
        with self._lock:
            return {
                "closed": self._closed,
                "traces_started": self._traces_started,
                "traces_finished": self._traces_finished,
                "spans_started": self._spans_started,
                "spans_finished": self._spans_finished,
                "errors": self._errors,
                "recent_spans": [
                    {
                        "trace_id": item.trace_id,
                        "span_id": item.span_id,
                        "kind": item.kind,
                        "has_error": item.has_error,
                    }
                    for item in self._recent
                ],
            }
