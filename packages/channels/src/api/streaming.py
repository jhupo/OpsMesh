"""Own request dependencies through response-stream completion."""

from contextlib import nullcontext

import anyio
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

_CLEANUP_TIMEOUT_SECONDS = 10


async def close_response(close, failure: BaseException | None) -> None:
    """Bound teardown and preserve the original transport failure."""
    try:
        with anyio.fail_after(_CLEANUP_TIMEOUT_SECONDS, shield=True):
            await close(failure)
    except BaseException as error:
        if failure is None:
            raise
        failure.add_note(f"Response cleanup failed: {type(error).__name__}")


class MessageStreamingResponse(StreamingResponse):
    """Run message dispatch with a bounded queue and ASGI backpressure."""

    def __init__(
        self,
        producer,
        *,
        capacity: int,
        on_close,
        on_written=None,
        execution_scope=nullcontext,
    ) -> None:
        """Keep dispatch in the response lifetime with bounded buffering."""
        super().__init__(
            iter(()),
            headers={
                "content-type": "text/event-stream; charset=utf-8",
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
        )
        self._on_close = on_close
        self._execution_scope = execution_scope
        self._closed = False
        self.failure: BaseException | None = None
        self._on_written = on_written
        self._producer = producer
        self._capacity = capacity

    async def stream_response(self, send: Send) -> None:
        """Cancel dispatch and its dependencies when transport sending fails."""
        with self._execution_scope():
            await self._send_frames(send)

    async def _send_frames(self, send: Send) -> None:
        from .output import OutputFrame

        sender, receiver = anyio.create_memory_object_stream[
            OutputFrame | bytes
        ](self._capacity)

        async def produce() -> None:
            with self._execution_scope():
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
                        body = (
                            frame.body
                            if isinstance(frame, OutputFrame)
                            else frame
                        )
                        await send(
                            {
                                "type": "http.response.body",
                                "body": body,
                                "more_body": True,
                            }
                        )
                        if isinstance(frame, OutputFrame) and self._on_written:
                            await self._on_written(frame)
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
            finally:
                tasks.cancel_scope.cancel()

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

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Use Starlette for sending and guarantee cleanup on every exit."""
        with self._execution_scope():
            error = None
            try:
                await super().__call__(scope, receive, send)
            except BaseException as failure:
                error = failure
                raise
            finally:
                await close_response(self.aclose, error or self.failure)


class MessageResponse(Response):
    """Keep plugin ownership until a materialized JSON response is written."""

    def __init__(self, response, *, on_close, execution_scope):
        """Retain the encoded response and its transport finalizer."""
        self._on_close = on_close
        self._execution_scope = execution_scope
        super().__init__(
            content=response.body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
            background=response.background,
        )

    async def __call__(self, scope, receive, send):
        """Observe actual writes and finalize even on disconnect."""
        with self._execution_scope():
            failure = None
            try:
                await super().__call__(scope, receive, send)
            except BaseException as error:
                failure = error
                raise
            finally:
                await close_response(self._on_close, failure)
