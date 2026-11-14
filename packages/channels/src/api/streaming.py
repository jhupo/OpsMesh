"""Own request dependencies through response-stream completion."""

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, AsyncExitStack, nullcontext

import anyio
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

_CLEANUP_TIMEOUT_SECONDS = 10
_ExecutionScope = Callable[[], AbstractContextManager[None]]


class StreamLease:
    """Release a response source before its request-scoped dependencies."""

    def __init__(self, dependencies: AsyncExitStack) -> None:
        """Take ownership of the request dependency stack."""
        self._dependencies = dependencies

    async def close(self, error: BaseException | None = None) -> None:
        """Close dependencies within their own cancellation shield."""
        try:
            with anyio.fail_after(_CLEANUP_TIMEOUT_SECONDS, shield=True):
                if error is None:
                    await self._dependencies.aclose()
                else:
                    await self._dependencies.__aexit__(
                        type(error), error, error.__traceback__
                    )
        except BaseException as cleanup_error:
            if error is None:
                raise
            error.add_note(f"HTTP dependency cleanup failed: {cleanup_error!r}")
            logger.exception("HTTP request dependency cleanup failed")

    async def release(
        self, response: object, error: BaseException | None
    ) -> None:
        """Always release dependencies, including after source timeout."""
        failure = error
        try:
            if isinstance(response, StreamingResponse):
                with anyio.fail_after(_CLEANUP_TIMEOUT_SECONDS, shield=True):
                    if isinstance(response, ManagedStreamingResponse):
                        await response.aclose(error)
                        failure = failure or response.failure
                    else:
                        close = getattr(response.body_iterator, "aclose", None)
                        if close is not None:
                            await close()
        except BaseException as cleanup_error:
            failure = failure or cleanup_error
            if error is not None:
                error.add_note(f"HTTP stream cleanup failed: {cleanup_error!r}")
            logger.exception("HTTP response stream cleanup failed")
            if error is None:
                raise
        finally:
            await self.close(failure)

    def transfer(
        self,
        response: Response,
        *,
        execution_scope: _ExecutionScope = nullcontext,
    ) -> Response:
        """Transfer ownership to the driver until the response is closed."""
        if not isinstance(response, StreamingResponse):
            raise TypeError("A streaming response is required")

        async def close(error: BaseException | None) -> None:
            await self.release(response, error)

        return ManagedStreamingResponse(
            response.body_iterator,
            status_code=response.status_code,
            headers=dict(response.headers),
            background=response.background,
            on_close=close,
            execution_scope=execution_scope,
        )


class ManagedStreamingResponse(StreamingResponse):
    """Close a native response's dependencies on completion or disconnect."""

    def __init__(
        self,
        content,
        *,
        on_close: Callable[[BaseException | None], Awaitable[None]],
        execution_scope: _ExecutionScope = nullcontext,
        **kwargs,
    ) -> None:
        """Associate response ownership with its ASGI send lifetime."""
        super().__init__(content, **kwargs)
        self._on_close = on_close
        self._execution_scope = execution_scope
        self._closed = False
        self.failure: BaseException | None = None

    async def listen_for_disconnect(self, receive: Receive) -> None:
        """Preserve disconnect information for framework dependency teardown."""
        await super().listen_for_disconnect(receive)
        self.failure = ConnectionError("HTTP client disconnected")

    async def aclose(self, error: BaseException | None = None) -> None:
        """Release the response exactly once within a bounded shield."""
        if self._closed:
            return
        self._closed = True
        self.failure = error or self.failure
        with (
            self._execution_scope(),
            anyio.fail_after(_CLEANUP_TIMEOUT_SECONDS, shield=True),
        ):
            await self._on_close(self.failure)

    async def stream_response(self, send: Send) -> None:
        """Enter admission in Starlette's actual response-sending task."""
        with self._execution_scope():
            await super().stream_response(send)

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Use Starlette for sending and guarantee cleanup on every exit."""
        with self._execution_scope():
            error = None
            try:
                if scope.get("method") == "HEAD":
                    await Response(
                        status_code=self.status_code, headers=dict(self.headers)
                    )(scope, receive, send)
                else:
                    await super().__call__(scope, receive, send)
            except BaseException as failure:
                error = failure
                raise
            finally:
                await self.aclose(error)


class MessageStreamingResponse(ManagedStreamingResponse):
    """Run message dispatch with a bounded queue and ASGI backpressure."""

    def __init__(self, producer, *, capacity: int, on_close) -> None:
        """Keep dispatch in the response lifetime with bounded buffering."""
        super().__init__(
            iter(()),
            on_close=on_close,
            headers={
                "content-type": "text/event-stream; charset=utf-8",
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
        )
        self._producer = producer
        self._capacity = capacity

    async def stream_response(self, send: Send) -> None:
        """Cancel dispatch and its dependencies when transport sending fails."""
        sender, receiver = anyio.create_memory_object_stream[bytes](
            self._capacity
        )

        async def produce() -> None:
            async with sender:
                await self._producer(sender.send)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(produce)
            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": self.status_code,
                        "headers": self.raw_headers,
                    }
                )
                async with receiver:
                    async for frame in receiver:
                        await send(
                            {
                                "type": "http.response.body",
                                "body": frame,
                                "more_body": True,
                            }
                        )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
            finally:
                tasks.cancel_scope.cancel()
