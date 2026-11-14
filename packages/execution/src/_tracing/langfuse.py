"""Export native OpenInference spans to Langfuse through OTLP."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opsmesh.utils import run_sync

from ..config import LangfuseSettings
from ..errors import TracingConfigurationError
from .context import TraceContext
from .exporter import ExportDiagnostics

if TYPE_CHECKING:
    from openinference.instrumentation.openai_agents import (
        OpenAIAgentsInstrumentor,
    )
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)


class LangfuseBridge:
    """Own export resources without changing the global OTel provider."""

    def __init__(
        self,
        settings: LangfuseSettings,
        *,
        include_sensitive_data: bool = False,
    ) -> None:
        self._settings = settings
        self._include_sensitive_data = include_sensitive_data
        self._provider: TracerProvider | None = None
        self._instrumentor: OpenAIAgentsInstrumentor | None = None
        self._exporter: ExportDiagnostics | None = None
        self._export_snapshot: dict[str, Any] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._stopping = False

    def install(self) -> None:
        """Install one SDK instrumentor and roll back failed initialization."""
        if self._stopping:
            raise TracingConfigurationError(
                "The previous Langfuse bridge has not stopped"
            )
        if self._instrumentor is not None:
            return
        public_key = _secret(self._settings.public_key_ref)
        secret_key = _secret(self._settings.secret_key_ref)
        try:
            from openinference.instrumentation import TraceConfig
            from openinference.instrumentation.openai_agents import (
                OpenAIAgentsInstrumentor,
            )

            from .attributes import TraceAttributeProcessor
        except ImportError as exc:
            raise TracingConfigurationError(
                "Langfuse tracing requires the pinned Langfuse "
                "and OpenInference packages"
            ) from exc
        instrumentor = OpenAIAgentsInstrumentor()
        if instrumentor.is_instrumented_by_opentelemetry:
            raise TracingConfigurationError(
                "OpenAI Agents instrumentation is already owned "
                "by another component"
            )
        provider = self._create_provider(public_key, secret_key)
        try:
            provider.add_span_processor(TraceAttributeProcessor())
            instrumentor.instrument(
                tracer_provider=provider,
                config=TraceConfig(
                    hide_inputs=not self._include_sensitive_data,
                    hide_outputs=not self._include_sensitive_data,
                ),
            )
            if not instrumentor.is_instrumented_by_opentelemetry:
                raise TracingConfigurationError(
                    "OpenAI Agents instrumentation did not initialize"
                )
        except BaseException as exc:
            try:
                if instrumentor.is_instrumented_by_opentelemetry:
                    instrumentor.uninstrument()
                else:
                    # BaseInstrumentor marks success only after patching.
                    # Its pinned rollback also handles partial setup.
                    instrumentor._uninstrument()
            finally:
                provider.shutdown()
            if isinstance(exc, Exception):
                raise TracingConfigurationError(
                    "Unable to initialize OpenAI Agents instrumentation"
                ) from exc
            raise
        self._provider = provider
        self._instrumentor = instrumentor

    def _create_provider(
        self, public_key: str, secret_key: str
    ) -> TracerProvider:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import (
            ParentBased,
            TraceIdRatioBased,
        )

        provider = TracerProvider(
            sampler=ParentBased(TraceIdRatioBased(self._settings.sample_rate))
        )
        authentication = base64.b64encode(
            f"{public_key}:{secret_key}".encode()
        ).decode("ascii")
        try:
            exporter = ExportDiagnostics(
                OTLPSpanExporter(
                    endpoint=f"{self._settings.base_url.rstrip('/')}/api/public/otel/v1/traces",
                    headers={
                        "Authorization": f"Basic {authentication}",
                        "x-langfuse-ingestion-version": "4",
                    },
                    timeout=self._settings.export_timeout_seconds,
                )
            )
            try:
                processor = BatchSpanProcessor(
                    exporter,
                    export_timeout_millis=int(
                        self._settings.export_timeout_seconds * 1000
                    ),
                )
            except BaseException:
                exporter.shutdown()
                raise
            provider.add_span_processor(processor)
            self._exporter = exporter
        except BaseException as exc:
            provider.shutdown()
            raise TracingConfigurationError(
                "Unable to initialize the Langfuse OTLP exporter"
            ) from exc
        return provider

    @contextmanager
    def scope(
        self,
        context: TraceContext,
        sdk_trace_id: str | None,
        plugin_id: str | None,
    ) -> Iterator[None]:
        """Attach execution dimensions to every span without a second trace."""
        from .attributes import TraceAttributes, attribute_scope

        with attribute_scope(
            TraceAttributes(
                context=context,
                sdk_trace_id=sdk_trace_id,
                plugin_id=plugin_id,
                environment=self._settings.environment,
            )
        ):
            yield

    async def shutdown(self) -> None:
        """Retain resources and one close task until release is confirmed."""
        self._stopping = True
        if self._instrumentor is not None:
            self._instrumentor.uninstrument()
            self._instrumentor = None
        if self._provider is not None:
            if self._shutdown_task is None:
                self._shutdown_task = asyncio.create_task(
                    run_sync(self._close_provider)(self._provider)
                )
                self._shutdown_task.add_done_callback(_observe_shutdown)
            await asyncio.wait_for(
                asyncio.shield(self._shutdown_task),
                timeout=self._settings.shutdown_timeout_seconds,
            )
            if self._exporter is not None:
                self._export_snapshot = self._exporter.snapshot()
            self._exporter = None
            self._provider = None
        self._shutdown_task = None
        self._stopping = False

    def _close_provider(self, provider: TracerProvider) -> None:
        """Flush and close sequentially in one retained worker task."""
        try:
            flushed = provider.force_flush(
                timeout_millis=int(self._settings.export_timeout_seconds * 1000)
            )
            if not flushed:
                logger.warning("Langfuse trace flush did not complete")
        except Exception:
            logger.warning("Langfuse trace flush failed")
        finally:
            provider.shutdown()

    def diagnostics(self) -> dict[str, Any]:
        """Expose cleanup progress and actual exporter outcomes safely."""
        task = self._shutdown_task
        return {
            "instrumented": self._instrumentor is not None,
            "provider_active": self._provider is not None,
            "shutdown_pending": task is not None and not task.done(),
            "shutdown_failed": task is not None
            and task.done()
            and (task.cancelled() or task.exception() is not None),
            "exporter": self._exporter.snapshot()
            if self._exporter is not None
            else (
                dict(self._export_snapshot)
                if self._export_snapshot is not None
                else None
            ),
        }


def _observe_shutdown(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Langfuse exporter shutdown failed")


def _secret(reference: str) -> str:
    value = os.environ.get(reference)
    if not value:
        raise TracingConfigurationError(
            f"Missing tracing credential environment variable {reference}"
        )
    return value
