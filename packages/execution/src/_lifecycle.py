"""Own optional tracing resources for native SDK execution."""

from typing import Any

from opsmesh import on_shutdown, on_startup
from opsmesh.plugins.config import get_plugin_config

from ._tracing import TracingSupport
from .config import Settings
from .errors import ExecutionClosed


class ExecutionRuntime:
    """Lifecycle resources without a persistence or user dependency."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tracing = TracingSupport(settings.tracing)
        self.started = False

    async def startup(self) -> None:
        if self.started:
            return
        self.tracing.install()
        self.started = True

    async def shutdown(self) -> None:
        if not self.started:
            return
        await self.tracing.shutdown()
        self.started = False


_runtime: ExecutionRuntime | None = None


def get_runtime() -> ExecutionRuntime:
    """Return resources only while the plugin is running."""
    if _runtime is None or not _runtime.started:
        raise ExecutionClosed("The execution plugin is not running")
    return _runtime


@on_startup
async def startup() -> None:
    global _runtime
    runtime = ExecutionRuntime(get_plugin_config(Settings))
    await runtime.startup()
    _runtime = runtime


@on_shutdown
async def shutdown() -> None:
    global _runtime
    runtime = _runtime
    if runtime is not None:
        await runtime.shutdown()
        _runtime = None


def diagnostics() -> dict[str, Any]:
    """Return a detached, content-free snapshot of tracing diagnostics."""
    return get_runtime().tracing.diagnostics()
