"""Own active execution tasks and optional tracing resources."""

from opsmesh import on_shutdown, on_startup, on_stopping
from opsmesh.plugins.config import get_plugin_config

from ._calls import ActiveCalls
from ._tracing import TracingSupport
from .config import Settings
from .errors import ExecutionClosed


class ExecutionRuntime:
    """Lifecycle resources without a persistence or user dependency."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls = ActiveCalls()
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
        try:
            await self.calls.close(self.settings.shutdown_timeout_seconds)
        finally:
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


@on_stopping
async def stopping() -> None:
    if _runtime is not None:
        await _runtime.calls.close(_runtime.settings.shutdown_timeout_seconds)


@on_shutdown
async def shutdown() -> None:
    global _runtime
    runtime, _runtime = _runtime, None
    if runtime is not None:
        await runtime.shutdown()
