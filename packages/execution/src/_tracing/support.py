"""Install the selected SDK tracing destination exactly once."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from typing import Any

from agents import RunConfig
from agents.tracing import (
    flush_traces,
    get_current_trace,
    set_trace_processors,
    set_tracing_disabled,
    trace,
)

from ..config import TracingSettings
from ..errors import TracingConfigurationError
from .context import TraceContext
from .langfuse import LangfuseBridge
from .local import LocalTraceProcessor

logger = logging.getLogger(__name__)


class TracingSupport:
    """Own the process-wide tracing destination and its diagnostics."""

    def __init__(self, settings: TracingSettings) -> None:
        """Select one tracing destination for this process."""
        self._settings = settings
        self._installed = False
        self._processor: LocalTraceProcessor | None = None
        self._bridge: LangfuseBridge | None = None
        self._langfuse_snapshot: dict[str, Any] | None = None

    @property
    def installed(self) -> bool:
        """Return whether the configured destination is active."""
        return self._installed

    def install(self) -> None:
        """Replace non-selected exporters with the configured target."""
        if self._installed:
            return
        destination = self._settings.destination
        if destination == "local":
            processor = LocalTraceProcessor()
            set_tracing_disabled(False)
            set_trace_processors([processor])
            self._processor = processor
        elif destination == "langfuse":
            settings = self._settings.langfuse
            if settings is None:
                raise TracingConfigurationError(
                    "langfuse tracing requires tracing.langfuse settings"
                )
            bridge = LangfuseBridge(
                settings,
                include_sensitive_data=self._settings.include_sensitive_data,
            )
            bridge.install()
            set_tracing_disabled(False)
            self._bridge = bridge
        else:
            set_trace_processors([])
            set_tracing_disabled(True)
        self._installed = True

    def prepare(self, run_config: RunConfig | None) -> RunConfig:
        """Copy SDK configuration while enforcing host tracing limits."""
        config = run_config if run_config is not None else RunConfig()
        return replace(
            config,
            tracing_disabled=(
                self._settings.destination == "disabled"
                or config.tracing_disabled
            ),
            trace_include_sensitive_data=(
                self._settings.include_sensitive_data
                and config.trace_include_sensitive_data
            ),
        )

    @contextmanager
    def scope(
        self,
        run_config: RunConfig,
        trace_context: TraceContext | None = None,
        plugin_id: str | None = None,
        *,
        resume: bool = False,
    ) -> Iterator[str | None]:
        """Create a native trace before execution accounting starts.

        Existing traces remain owned by their caller. The context must stay open
        until all stream events and their cleanup have completed.
        Resumed state remains owned by Runner; its trace identifier may only
        become available inside SDK hooks.
        """
        if (
            run_config.tracing_disabled
            or self._settings.destination == "disabled"
        ):
            yield None
            return
        existing = get_current_trace()
        context = trace_context or TraceContext()
        if context.session_id is None and run_config.group_id is not None:
            context = replace(context, session_id=run_config.group_id)
        if resume and existing is None:
            with ExitStack() as stack:
                if self._bridge is not None:
                    stack.enter_context(
                        self._bridge.scope(
                            context, run_config.trace_id, plugin_id
                        )
                    )
                yield None
            return
        current = existing or trace(
            workflow_name=run_config.workflow_name,
            trace_id=run_config.trace_id,
            group_id=run_config.group_id or context.session_id,
            metadata=run_config.trace_metadata,
            tracing=run_config.tracing,
        )
        with ExitStack() as stack:
            if self._bridge is not None:
                stack.enter_context(
                    self._bridge.scope(context, current.trace_id, plugin_id)
                )
            if existing is None:
                stack.enter_context(current)
            yield current.trace_id

    async def shutdown(self) -> None:
        """Flush owned tracing state and release optional clients."""
        if not self._installed:
            return
        if self._processor is not None:
            try:
                flush_traces()
                self._processor.force_flush()
                self._processor.shutdown()
            except Exception:
                logger.warning("SDK trace flush did not complete")
        if self._bridge is not None:
            await self._bridge.shutdown()
            self._langfuse_snapshot = self._bridge.diagnostics()
            self._bridge = None
        set_trace_processors([])
        set_tracing_disabled(True)
        self._installed = False

    def snapshot(self) -> dict[str, Any] | None:
        """Return local diagnostics when the local destination is active."""
        if self._processor is None:
            return None
        return self._processor.snapshot()

    def diagnostics(self) -> dict[str, Any]:
        """Describe the active destination without exposing exporter clients."""
        return {
            "destination": self._settings.destination,
            "installed": self._installed,
            "local": self.snapshot(),
            "langfuse": self._bridge.diagnostics()
            if self._bridge is not None
            else deepcopy(self._langfuse_snapshot),
        }
