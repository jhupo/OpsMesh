"""Keep admitted plugin calls alive through their dependency finalizers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import anyio

from .model import Plugin


class PluginUnavailable(RuntimeError):
    """The requested plugin cannot accept another independent call."""


@dataclass(eq=False)
class PluginCall:
    """One admitted invocation retained until dependency cleanup completes."""

    done: anyio.Event = field(default_factory=anyio.Event)
    tasks: set[asyncio.Task] = field(default_factory=set)
    plugins: set[Plugin] = field(default_factory=set)
    active: bool = True
    cancel_scopes: dict[asyncio.Task, set[anyio.CancelScope]] = field(
        default_factory=dict, repr=False
    )
    _stack: AsyncExitStack | None = field(default=None, repr=False)


_current_call: ContextVar[PluginCall | None] = ContextVar(
    "plugin_invocation", default=None
)


def _dependencies(plugin: Plugin) -> set[Plugin]:
    from .registry import get_plugin

    result: set[Plugin] = set()

    def visit(item: Plugin) -> None:
        if item in result:
            return
        result.add(item)
        for name in item.dependencies:
            dependency = get_plugin(name)
            if dependency is None:
                raise PluginUnavailable(f"Missing plugin dependency: {name}")
            visit(dependency)

    visit(plugin)
    return result


def hold_plugin(
    plugin: Plugin | None, stack: AsyncExitStack
) -> PluginCall | None:
    """Atomically retain a dependency closure until the stack closes."""
    if plugin is None:
        return None
    if not isinstance(plugin, Plugin):
        raise TypeError("Invocation ownership requires a Plugin")
    key = id(stack)
    existing = plugin.calls.get(key)
    if existing is not None:
        return existing
    inherited = _current_call.get()
    task = asyncio.current_task()
    dependencies = _dependencies(plugin)
    if inherited is not None and not inherited.active:
        raise PluginUnavailable("The inherited invocation has ended")
    if (
        inherited is not None
        and task in inherited.tasks
        and dependencies <= inherited.plugins
    ):
        return inherited
    unavailable = [item.id_ for item in dependencies if not item.ready]
    if unavailable:
        raise PluginUnavailable(
            f"Plugins are not ready: {', '.join(unavailable)}"
        )
    return _retain_dependencies(dependencies, stack)


def _retain_dependencies(
    dependencies: set[Plugin], stack: AsyncExitStack
) -> PluginCall:
    key = id(stack)
    # Retained admissions must keep their stack identity alive after a failure.
    call = PluginCall(plugins=dependencies, _stack=stack)
    task = asyncio.current_task()
    if task is not None:
        call.tasks.add(task)
    for item in dependencies:
        item.calls[key] = call

    def release() -> None:
        call.active = False
        for item in dependencies:
            item.calls.pop(key, None)
        call.done.set()
        call._stack = None

    stack.callback(release)
    return call


@contextmanager
def invocation_scope(call: PluginCall | None) -> Iterator[None]:
    """Associate executing code with its admission without owning cleanup."""
    task = asyncio.current_task()
    if call is not None:
        if not call.active:
            raise PluginUnavailable("The invocation has ended")
        if task is not None:
            call.tasks.add(task)
    with anyio.CancelScope() as scope:
        if call is not None and task is not None:
            call.cancel_scopes.setdefault(task, set()).add(scope)
        token = _current_call.set(call)
        try:
            yield
        finally:
            _current_call.reset(token)
            if call is not None and task is not None:
                scopes = call.cancel_scopes[task]
                scopes.discard(scope)
                if not scopes:
                    del call.cancel_scopes[task]
    if scope.cancelled_caught:
        raise anyio.get_cancelled_exc_class()


@asynccontextmanager
async def plugin_call(plugin_id: str) -> AsyncIterator[PluginCall]:
    """Retain a ready plugin and its providers for a complete operation."""
    from .registry import get_plugin

    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise PluginUnavailable(f"Unknown plugin: {plugin_id}")
    async with AsyncExitStack() as stack:
        call = hold_plugin(plugin, stack)
        assert call is not None
        with invocation_scope(call):
            yield call


@asynccontextmanager
async def continued_call(
    origin: PluginCall, plugin_id: str
) -> AsyncIterator[PluginCall]:
    """Pin an admitted native continuation before its parent call ends.

    Only framework bridges may explicitly continue an already admitted
    dependency subset. The new occupation has an independent lifetime and
    does not grant ordinary child tasks inherited admission.
    """
    async with AsyncExitStack() as stack:
        call = hold_continuation(origin, plugin_id, stack)
        with invocation_scope(call):
            yield call


def hold_continuation(
    origin: PluginCall, plugin_id: str, stack: AsyncExitStack
) -> PluginCall:
    """Retain an explicit continuation until its transferable stack closes."""
    from .registry import get_plugin

    if not origin.active:
        raise PluginUnavailable("The originating invocation has ended")
    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise PluginUnavailable(f"Unknown plugin: {plugin_id}")
    dependencies = _dependencies(plugin)
    if not dependencies <= origin.plugins:
        raise PluginUnavailable(
            "Continuation exceeds its admitted dependencies"
        )
    return _retain_dependencies(dependencies, stack)


async def drain_plugin(plugin: Plugin, timeout: float) -> None:
    """Wait for accepted work, then cancel and wait for its finalizers."""
    with anyio.move_on_after(timeout / 2):
        while plugin.calls:
            for call in tuple(set(plugin.calls.values())):
                await call.done.wait()
    if not plugin.calls:
        return
    current = asyncio.current_task()
    with anyio.fail_after(timeout / 2, shield=True):
        while plugin.calls:
            calls = tuple(set(plugin.calls.values()))
            for call in calls:
                for task in call.tasks:
                    if task is not current and not task.done():
                        scopes = call.cancel_scopes.get(task)
                        if scopes:
                            for scope in tuple(scopes):
                                scope.cancel()
                        else:
                            task.cancel()
            for call in calls:
                await call.done.wait()
