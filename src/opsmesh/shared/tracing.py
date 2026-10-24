import logging
from typing import Any

from agents import set_trace_processors
from agents.tracing import TracingProcessor

logger = logging.getLogger("opsmesh.tracing")


class LocalTracing(TracingProcessor):
    def on_trace_start(self, trace: Any) -> None:
        logger.info("trace.started", extra={"trace_id": trace.trace_id})

    def on_trace_end(self, trace: Any) -> None:
        logger.info("trace.finished", extra={"trace_id": trace.trace_id})

    def on_span_start(self, span: Any) -> None:
        pass

    def on_span_end(self, span: Any) -> None:
        logger.info("span.finished", extra={"trace_id": span.trace_id, "span_id": span.span_id})

    def shutdown(self) -> None:
        self.force_flush()

    def force_flush(self) -> None:
        for handler in logger.handlers:
            handler.flush()


def configure() -> LocalTracing:
    processor = LocalTracing()
    set_trace_processors([processor])
    return processor
