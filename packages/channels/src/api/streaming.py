"""Own request dependencies through response-stream completion."""

import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack

import anyio
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

_CLEANUP_TIMEOUT_SECONDS = 10


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

    def transfer(self, response: Response) -> Response:
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
        )


class ManagedStreamingResponse(StreamingResponse):
    """Close a native response's dependencies on completion or disconnect."""

    def __init__(
        self,
        content,
        *,
        on_close: Callable[[BaseException | None], Awaitable[None]],
        **kwargs,
    ) -> None:
        """Associate response ownership with its ASGI send lifetime."""
        super().__init__(content, **kwargs)
        self._on_close = on_close
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
        with anyio.fail_after(_CLEANUP_TIMEOUT_SECONDS, shield=True):
            await self._on_close(self.failure)

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Use Starlette for sending and guarantee cleanup on every exit."""
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
