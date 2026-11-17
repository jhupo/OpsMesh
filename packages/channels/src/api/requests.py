"""Bound request ingestion before channel-specific payload decoding."""

import logging
from contextlib import AsyncExitStack, contextmanager

import anyio
from starlette.exceptions import HTTPException
from starlette.requests import Request

from opsmesh.plugins.invocation import hold_plugin, invocation_scope
from opsmesh.plugins.registry import plugin_scope

_CLEANUP_TIMEOUT_SECONDS = 10
logger = logging.getLogger(__name__)


async def read_body(request: Request, limit: int) -> bytes:
    """Read bounded bytes, including requests without a length header."""
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Request body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


class RequestScope:
    """Carry admission and one deadline across request and response tasks."""

    def __init__(self, owner, stack: AsyncExitStack, timeout: float) -> None:
        """Acquire admission and start the ingestion/execution deadline."""
        self.owner = owner
        self.deadline = anyio.current_time() + timeout
        self.admitted_call = hold_plugin(owner, stack)

    @contextmanager
    def activate(self):
        """Reenter framework ownership in the task doing the work."""
        with plugin_scope(self.owner), invocation_scope(self.admitted_call):
            yield

    @contextmanager
    def budget(self):
        """Use the remaining request budget without sharing cancel scopes."""
        remaining = self.deadline - anyio.current_time()
        if remaining <= 0:
            raise TimeoutError("Request deadline exceeded")
        with anyio.fail_after(remaining):
            yield


async def parse_message(request: Request, limit: int):
    """Read the fixed JSON message contract within the request budget."""
    import json

    from pydantic import ValidationError

    from .errors import HTTPError
    from .messages import MessageRequest

    content = await read_body(request, limit)
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if (
        content
        and content_type
        and not (
            content_type == "application/json" or content_type.endswith("+json")
        )
    ):
        raise HTTPError(415, "request content type must be JSON")
    try:
        payload = json.loads(content) if content else {}
    except (ValueError, UnicodeDecodeError):
        raise HTTPError(400, "request body is not valid JSON") from None
    try:
        return MessageRequest.model_validate(payload)
    except ValidationError:
        raise HTTPError(
            422, "request body does not match the contract"
        ) from None


class RequestResources:
    """Close handler dependencies before transport admission is released."""

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
