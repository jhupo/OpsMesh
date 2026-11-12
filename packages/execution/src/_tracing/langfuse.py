"""Export native OpenInference spans to Langfuse through OTLP."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ..config import LangfuseSettings
from ..errors import TracingConfigurationError
from .context import TraceContext

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

    def install(self) -> None:
        """Install one SDK instrumentor and roll back failed initialization."""
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
            exporter = OTLPSpanExporter(
                endpoint=f"{self._settings.base_url.rstrip('/')}/api/public/otel/v1/traces",
                headers={
                    "Authorization": f"Basic {authentication}",
                    "x-langfuse-ingestion-version": "4",
                },
                timeout=self._settings.export_timeout_seconds,
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
        """Stop SDK instrumentation and flush the owned export provider."""
        instrumentor, provider = self._instrumentor, self._provider
        self._instrumentor = None
        self._provider = None
        if instrumentor is not None:
            try:
                instrumentor.uninstrument()
            except Exception:
                logger.warning("Unable to uninstrument the Langfuse bridge")
        if provider is not None:
            try:
                flushed = await asyncio.to_thread(
                    provider.force_flush,
                    timeout_millis=int(
                        self._settings.export_timeout_seconds * 1000
                    ),
                )
                if not flushed:
                    logger.warning("Langfuse trace flush did not complete")
            except Exception:
                logger.warning("Langfuse trace flush failed")
            finally:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(provider.shutdown),
                        timeout=self._settings.shutdown_timeout_seconds,
                    )
                except Exception:
                    logger.warning(
                        "Langfuse exporter shutdown did not complete"
                    )


def _secret(reference: str) -> str:
    value = os.environ.get(reference)
    if not value:
        raise TracingConfigurationError(
            f"Missing tracing credential environment variable {reference}"
        )
    return value
