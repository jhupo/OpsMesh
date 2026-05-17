from fastapi import FastAPI

from backend.app.api.errors import register_error_handlers
from backend.app.api.router import api_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import configure_logging
from backend.app.core.middleware import RequestContextMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    app = FastAPI(
        title="ChainCloud Agent Team API",
        version="0.1.0",
        docs_url="/docs" if app_settings.enable_api_docs else None,
        redoc_url="/redoc" if app_settings.enable_api_docs else None,
        openapi_url="/openapi.json" if app_settings.enable_api_docs else None,
    )
    app.state.settings = app_settings
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(api_router, prefix=app_settings.api_prefix)
    return app


app = create_app()
