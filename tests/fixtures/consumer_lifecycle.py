"""Run one plugin's own lifecycle entries without starting its providers."""

import anyio

from opsmesh.plugins.model import PluginStatus
from opsmesh.plugins.registry import get_plugin


def _mark_dependencies_ready(plugin):
    """Emulate providers that a host would have started already."""
    pending = list(plugin.dependencies)
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        dependency = get_plugin(name)
        if dependency is None:
            continue
        if dependency.status is PluginStatus.LOADED:
            dependency.status = PluginStatus.READY
        pending.extend(dependency.dependencies)


async def run(plugin, driver, body, *, skip=None, started=()):
    """Drive the plugin's startup, readiness, started and close entries.

    Only the consumer's own entries run. Startup callbacks that need a
    provider this process does not start are skipped by ``skip(func)``.
    Providers named in ``started`` run their own entries first.
    """
    lifespan = driver._lifespan
    lifespan._startup_funcs[:] = [
        entry
        for entry in lifespan._startup_funcs
        if skip is None or not skip(entry.func)
    ]
    _mark_dependencies_ready(plugin)
    providers = [get_plugin(name) for name in started]
    async with anyio.create_task_group() as task_group:
        lifespan._task_group = task_group
        try:
            for provider in providers:
                await lifespan._owned_phase(lifespan._startup_funcs, provider)
                await lifespan._owned_phase(lifespan._ready_funcs, provider)
                provider.status = PluginStatus.READY
                await lifespan._owned_phase(lifespan._started_funcs, provider)
            await lifespan._owned_phase(lifespan._startup_funcs, plugin)
            await lifespan._owned_phase(lifespan._ready_funcs, plugin)
            plugin.status = PluginStatus.READY
            await lifespan._owned_phase(lifespan._started_funcs, plugin)
            await body()
            plugin.status = PluginStatus.STOPPING
            await lifespan._close_plugin(plugin)
            plugin.status = PluginStatus.STOPPED
            for provider in reversed(providers):
                await lifespan._close_plugin(provider)
                provider.status = PluginStatus.STOPPED
        finally:
            lifespan._task_group = None
