import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from opsmesh.agents.routes import router as agents_router
from opsmesh.catalog.routes import router as catalog_router
from opsmesh.gateway.routes import router as gateway_router
from opsmesh.infra.database import new_id
from opsmesh.plugins.routes import router as plugins_router
from opsmesh.quotas.routes import router as quotas_router
from opsmesh.resources.routes import router as resources_router
from opsmesh.sessions.routes import router as sessions_router
from opsmesh.shared.context import bind_logging_context
from opsmesh.shared.errors import DomainError
from opsmesh.shared.models import AuditEvent
from opsmesh.shared.schemas import ErrorView
from opsmesh.tasks.routes import router as tasks_router
from opsmesh.users.routes import router as users_router

from .app import Application
from .initialization import initialize_admin

logger = logging.getLogger(__name__)


class UploadLimitMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0
        settings = scope["app"].state.application.settings
        limit = max(settings.file_limit_bytes, settings.package_limit_bytes) + 1_000_000

        async def bounded_receive() -> Any:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                from starlette.exceptions import HTTPException

                raise HTTPException(413, "Upload exceeds request limit")
            return message

        await self.app(scope, bounded_receive, send)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = Application.create()
        app.state.application = runtime
        try:

            def bootstrap() -> None:
                config = runtime.settings
                with runtime.sessions.begin() as db:
                    credentials = initialize_admin(
                        db,
                        config.admin_username,
                        config.admin_password.get_secret_value() if config.admin_password else None,
                    )
                if credentials is not None and credentials.generated:
                    print(
                        f"OpsMesh initial administrator\nUsername: {credentials.username}\nPassword: {credentials.password}",
                        flush=True,
                    )

            await asyncio.to_thread(bootstrap)
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="OpsMesh",
        version="0.2.0",
        lifespan=lifespan,
        responses={
            status: {"model": ErrorView} for status in (400, 401, 403, 404, 409, 413, 422, 429, 500)
        },
    )
    app.add_middleware(UploadLimitMiddleware)
    for router in [
        users_router,
        plugins_router,
        agents_router,
        catalog_router,
        resources_router,
        tasks_router,
        quotas_router,
        gateway_router,
        sessions_router,
    ]:
        app.include_router(router, prefix="/v1")

    @app.middleware("http")
    async def audit_request(request: Request, call_next: Any) -> Any:
        request_id = new_id()
        request.state.request_id = request_id
        with bind_logging_context(request_id=request_id):
            response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        fields = {
            "request_id": request_id,
            "method": request.method,
            "route": route,
            "status": response.status_code,
        }
        logger.info("http_request", extra=fields)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code >= 400:
            try:
                with request.app.state.application.sessions.begin() as db:
                    db.add(AuditEvent(user_id=getattr(request.state, "user_id", None), **fields))
            except Exception:
                logger.exception("audit_persistence_failed")
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            ErrorView(code=exc.code, message=exc.message, details=exc.details).model_dump(),
            status_code=exc.status_code,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(
            ErrorView(
                code="conflict", message="Resource already exists or is referenced"
            ).model_dump(),
            status_code=409,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            ErrorView(code=f"http_{exc.status_code}", message=str(exc.detail)).model_dump(),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_request_error", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(
            ErrorView(code="internal_error", message="Internal server error").model_dump(),
            status_code=500,
        )

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            ErrorView(code="invalid_argument", message="Invalid request values").model_dump(),
            status_code=422,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            ErrorView(
                code="validation_error",
                message="Request validation failed",
                details={
                    "fields": [
                        {"location": list(item["loc"]), "type": item["type"]}
                        for item in exc.errors()
                    ]
                },
            ).model_dump(),
            status_code=422,
        )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "alive"}

    return app
