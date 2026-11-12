"""Bound request ingestion before channel-specific payload decoding."""

from starlette.exceptions import HTTPException
from starlette.requests import Request


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
