"""Public Python entry points for hosting and configuring an OpsMesh platform."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from opsmesh._version import __version__

if TYPE_CHECKING:
    from opsmesh.bootstrap.application import OpsMeshApplication
    from opsmesh.bootstrap.settings import OpsMeshSettings


def __getattr__(name: str) -> Any:
    # Framework-only imports must not initialize the platform or its optional backends.
    if name == "OpsMeshApplication":
        from opsmesh.bootstrap.application import OpsMeshApplication

        return OpsMeshApplication
    if name == "OpsMeshSettings":
        from opsmesh.bootstrap.settings import OpsMeshSettings

        return OpsMeshSettings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_application(
    settings: OpsMeshSettings | None = None,
    *,
    secrets: Mapping[str, SecretStr] | None = None,
) -> OpsMeshApplication:
    """Compose public platform services; secret values never enter resource declarations."""
    from opsmesh.bootstrap.application import OpsMeshApplication

    return OpsMeshApplication(settings, secret_values=secrets)


def run(factory: Callable[..., AbstractAsyncContextManager[OpsMeshApplication]]) -> None:
    """Run a configured application factory with the standard publish/serve commands."""
    from opsmesh.entrypoints.cli.applications import run_application

    run_application(factory)


__all__ = ["OpsMeshApplication", "OpsMeshSettings", "__version__", "create_application", "run"]
