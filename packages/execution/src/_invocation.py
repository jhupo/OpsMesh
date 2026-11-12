"""One native invocation's callback, trace, and cleanup lifetime."""

import asyncio
from typing import Any

import anyio
from agents import (
    Agent,
    RunConfig,
    RunHooks,
    RunResult,
    RunResultStreaming,
    RunState,
)

from ._hooks import ExecutionHooks
from ._lifecycle import get_runtime
from ._streaming import close_stream, was_cancelled
from ._tracing import TraceContext
from .callbacks import ExecutionCallbacks


class Invocation:
    """Hold execution resources through consumption and terminal observers."""

    def __init__(
        self,
        agent: Agent[Any],
        message: Any,
        *,
        context: Any,
        hooks: RunHooks | None,
        run_config: RunConfig | None,
        plugin_id: str | None,
        trace_context: TraceContext | None,
    ) -> None:
        self.runtime = get_runtime()
        self.callbacks = ExecutionCallbacks(plugin_id)
        self.config = self.runtime.tracing.prepare(run_config)
        self.hooks = ExecutionHooks(hooks, callbacks=self.callbacks)
        self._trace = self.runtime.tracing.scope(
            self.config,
            trace_context,
            self.callbacks.plugin_id,
            resume=isinstance(message, RunState),
        )
        self.agent = agent
        self.message = message
        self.context = context
        self.result: RunResult | RunResultStreaming | None = None
        self.trace_id: str | None = None
        self._trace_entered = False
        self._active = None

    async def __aenter__(self):
        self._active = self.runtime.calls.begin()
        try:
            self.trace_id = self._trace.__enter__()
            self._trace_entered = True
            await self.callbacks.starting(
                {
                    "agent": self.agent,
                    "input": self.message,
                    "run_config": self.config,
                    "trace_id": self.trace_id,
                },
                self.context,
            )
        except BaseException as error:
            await self.__aexit__(type(error), error, error.__traceback__)
            raise
        return self

    async def __aexit__(self, error_type, error, traceback):
        failure = error
        notification_error = error
        timeout = self.runtime.settings.cleanup_timeout_seconds
        try:
            with anyio.CancelScope(shield=True):
                if isinstance(self.result, RunResultStreaming):
                    try:
                        cleaned = await close_stream(
                            self.result,
                            timeout,
                            cancel=error is not None
                            or not self.result.is_complete,
                        )
                        failure = failure or self.result.run_loop_exception
                        if not cleaned and failure is None:
                            failure = TimeoutError("SDK stream cleanup failed")
                        notification_error = failure
                        if notification_error is None and was_cancelled(
                            self.result
                        ):
                            notification_error = asyncio.CancelledError(
                                "Stream consumption ended before completion"
                            )
                    except BaseException as cleanup_error:
                        failure = self._combine(failure, cleanup_error)
                        notification_error = failure
                try:
                    with anyio.fail_after(timeout, shield=True):
                        await self.callbacks.close(failure)
                except BaseException as cleanup_error:
                    failure = self._combine(failure, cleanup_error)
                    notification_error = failure
                try:
                    await self.callbacks.finished(
                        {
                            "result": self.result,
                            "error": notification_error or failure,
                            "trace_id": self.trace_id,
                        },
                        self.context,
                        timeout,
                    )
                except BaseException as observer_error:
                    failure = self._combine(failure, observer_error)
        finally:
            try:
                if self._trace_entered:
                    self._trace.__exit__(
                        type(failure) if failure else None,
                        failure,
                        failure.__traceback__ if failure else None,
                    )
            finally:
                self.runtime.calls.end(self._active)
        if error is None and failure is not None:
            raise failure
        return False

    @staticmethod
    def _combine(original, cleanup):
        if original is None:
            return cleanup
        if cleanup is not original:
            original.add_note(f"Callback cleanup also failed: {cleanup!r}")
        return original
