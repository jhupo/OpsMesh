"""Own request dependencies through response-stream completion."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import anyio

from opsmesh.framework.drivers import Response, ResponseStream

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
            if isinstance(response, Response) and isinstance(
                response.content, ResponseStream
            ):
                with anyio.fail_after(_CLEANUP_TIMEOUT_SECONDS, shield=True):
                    await response.content.aclose(error)
                failure = failure or response.content.failure
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
        source = response.content
        if not isinstance(source, ResponseStream):
            raise TypeError("A streaming response is required")

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in source:
                    yield chunk
            except BaseException as error:
                source.failure = source.failure or error
                raise

        async def close(error: BaseException | None) -> None:
            await self.release(response, error or source.failure)

        return Response(
            response.status_code,
            headers=response.headers,
            content=ResponseStream(relay(), on_exit=close),
        )
