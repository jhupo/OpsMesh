"""HTTP composition: lifecycle, bounded inputs, safe errors and domain routers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opsmesh.framework.drivers.asgi import mount_driver_routes
from opsmesh.framework.errors import OpsMeshError

if TYPE_CHECKING:
    from opsmesh.bootstrap.application import OpsMeshApplication

logger = logging.getLogger(__name__)


def error_response(error: OpsMeshError, *, status: int | None = None) -> JSONResponse:
    code = error.code.upper()
    if status is None:
        if code in {"UNAUTHORIZED", "UNAUTHENTICATED", "BOT_AUTHENTICATION_FAILED"}:
            status = 401
        elif code in {
            "FORBIDDEN",
            "AUTHORIZATION_DENIED",
            "BOT_DISABLED",
            "PLUGIN_DISABLED",
            "RESOURCE_DISABLED",
        }:
            status = 403
        elif code in {"NOT_FOUND", "EXTENSION_NOT_FOUND"}:
            status = 404
        elif code in {"RATE_LIMITED", "BOT_RATE_LIMITED", "BUDGET_EXCEEDED"}:
            status = 429
        elif code in {
            "CONFLICT",
            "RESOURCE_IN_USE",
            "SESSION_BUSY",
            "IDEMPOTENCY_CONFLICT",
            "DELIVERY_UNKNOWN",
            "APPROVAL_REQUIRED",
            "EVENT_IN_PROGRESS",
            "SESSION_CLOSED",
        }:
            status = 409
        elif code in {
            "CONFIG_INVALID",
            "INVALID_REQUEST",
            "VALIDATION_ERROR",
            "FEATURE_UNSUPPORTED",
            "UNSUPPORTED_FEATURE",
            "CHECKPOINT_INCOMPATIBLE",
            "INPUT_TOO_LARGE",
            "BOT_ADAPTER_UNAVAILABLE",
            "BOT_REQUEST_REJECTED",
        }:
            status = 400
        elif code == "BOT_PROTOCOL_ERROR":
            status = 502
        elif error.retryable:
            status = 503
        else:
            status = 500
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    if status == 429:
        headers["Retry-After"] = "60"
    if error.info.retry_after_seconds is not None:
        headers["Retry-After"] = str(max(1, int(error.info.retry_after_seconds)))
    return JSONResponse(
        status_code=status,
        headers=headers,
        content={
            "error": {
                "error_id": error.info.error_id,
                "code": code,
                "message": error.message if status < 500 else "The request could not be completed",
                "retryable": error.retryable,
            }
        },
    )


class RequestBoundaryMiddleware:
    """Enforce a bounded body before parsing, and record outcomes without request content."""

    def __init__(self, app: ASGIApp, application: OpsMeshApplication) -> None:
        self.app, self.application = app, application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            chunks.append(chunk)
            size += len(chunk)
            if size > self.application.settings.max_http_body_bytes:
                response = error_response(
                    OpsMeshError("PAYLOAD_TOO_LARGE", "Request body is too large"), status=413
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        replayed = False
        status = 500

        async def bounded_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        async def response_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        started = perf_counter()
        carrier = {
            key.decode(): value.decode()
            for key, value in scope.get("headers", [])
            if key in {b"traceparent", b"tracestate"}
        }
        trace_scope = (
            self.application.traces.span(
                "http.request", carrier=carrier, attributes={"http.request.method": scope["method"]}
            )
            if self.application.traces
            else nullcontext()
        )
        try:
            with trace_scope:
                await self.app(scope, bounded_receive, response_send)
        finally:
            self.application.services.metrics.observe(
                "http_duration_seconds", perf_counter() - started
            )
            outcome = "success" if status < 400 else "rejected" if status < 500 else "failure"
            self.application.services.metrics.increment("http_requests", outcome)
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                try:
                    await self.application.services.audit.record(
                        f"http.{scope['method'].lower()}",
                        scope.get("path", ""),
                        actor_id=scope["state"].get("principal_id"),
                        outcome=outcome,
                        details={"status": status, "request_id": request_id},
                        event_id=request_id,
                    )
                except Exception:
                    logger.exception("HTTP audit persistence failed for request %s", request_id)


def create_http_app(application: OpsMeshApplication) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await application.start()
            mount_driver_routes(app, application.driver)
            yield
        finally:
            await application.close()

    app = FastAPI(title="OpsMesh API", version="0.1.0", lifespan=lifespan)
    app.state.opsmesh = application
    app.add_middleware(RequestBoundaryMiddleware, application=application)

    @app.exception_handler(OpsMeshError)
    async def known_error(request: Request, error: OpsMeshError) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "fields": [
                        {"location": list(item["loc"]), "type": item["type"]}
                        for item in error.errors()
                    ],
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(
            OpsMeshError("HTTP_ERROR", "HTTP request could not be processed"),
            status=error.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled HTTP failure", exc_info=error)
        return error_response(OpsMeshError("INTERNAL_ERROR", "The request could not be completed"))

    from .routes import routers

    for router in routers:
        app.include_router(router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        from sqlalchemy import text

        try:
            async with application.services.database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return app
