"""Host the API and bot gateway with one shared ASGI lifecycle."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opsmesh.bootstrap.application import OpsMeshApplication


async def serve(
    application: "OpsMeshApplication", *, host: str | None = None, port: int | None = None
) -> None:
    import uvicorn

    from .app import create_http_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_http_app(application),
            host=host or application.settings.host,
            port=port or application.settings.port,
            log_config=None,
        )
    )
    await server.serve()
