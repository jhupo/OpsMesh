"""HTTP and streaming entrypoint, composed from platform services."""

from .app import create_http_app

__all__ = ["create_http_app"]
