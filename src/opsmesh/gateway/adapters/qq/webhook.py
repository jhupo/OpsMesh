"""Bound the public ingress; signature verification remains in the native SDK."""

import re
from collections.abc import Iterator
from contextlib import contextmanager

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn import Server


class WebhookGuard:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        signature = headers.get(b"x-signature-ed25519")
        if signature is not None and re.fullmatch(rb"[0-9a-fA-F]{128}", signature) is None:
            await PlainTextResponse("Invalid signature", status_code=403)(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            frame = await receive()
            if frame["type"] == "http.disconnect":
                return
            body = frame.get("body", b"")
            size += len(body)
            if size > 1024 * 1024:
                await PlainTextResponse("Payload exceeds 1 MiB", status_code=413)(
                    scope, receive, send
                )
                return
            chunks.append(body)
            if not frame.get("more_body", False):
                break
        consumed = False

        async def bounded_receive() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


class WebhookServer(Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The gateway process owns signal handling; each channel only owns its listener.
        yield
