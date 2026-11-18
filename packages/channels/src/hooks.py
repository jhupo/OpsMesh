"""Observe and override channel operations within plugin admission."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

import anyio

from opsmesh.plugins.invocation import (
    PluginUnavailable,
    hold_plugin,
    invocation_scope,
)
from opsmesh.plugins.registry import (
    _current_plugin,
    get_plugin_by_module_name,
    plugin_scope,
    track_registration,
)
from opsmesh.utils import flatten_exception_group

if TYPE_CHECKING:
    from .channel import Channel

logger = logging.getLogger(__name__)

CallingHook = Callable[["Channel", str, dict[str, Any]], Awaitable[Any]]
CalledHook = Callable[
    ["Channel", Exception | None, str, dict[str, Any], Any], Awaitable[Any]
]
Hook = TypeVar("Hook", bound=Callable[..., Awaitable[Any]])


class MockCallException(Exception):
    """Replace an operation or its return value from a channel hook."""

    def __init__(self, result: Any) -> None:
        """Store the result supplied by the hook."""
        super().__init__(result)
        self.result = result


def register_hook(group: set[Hook], func: Hook) -> Hook:
    """Register a distinct callback with the declaring plugin's ownership."""
    owner = _current_plugin.get() or get_plugin_by_module_name(func.__module__)

    @wraps(func)
    async def registered(*args, **kwargs):
        async with AsyncExitStack() as stack:
            try:
                call = hold_plugin(owner, stack)
            except PluginUnavailable:
                return None
            with plugin_scope(owner), invocation_scope(call):
                return await func(*args, **kwargs)

    group.add(registered)
    track_registration(lambda: group.discard(registered), func)
    return func


async def invoke_operation(
    channel: Channel, operation: str, data: dict[str, Any]
) -> Any:
    """Run rejection and replacement hooks around an adapter operation."""
    failures: list[Exception] = []
    replacements: list[MockCallException] = []

    async def check(hook: CallingHook) -> None:
        try:
            await hook(channel, operation, data)
        except MockCallException as replacement:
            replacements.append(replacement)
        except Exception as error:
            failures.append(error)

    if channel._calling_hook:
        async with anyio.create_task_group() as tasks:
            for hook in tuple(channel._calling_hook):
                tasks.start_soon(check, hook)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(
                "Calling hooks rejected the operation", failures
            )
        if len(replacements) > 1:
            raise RuntimeError("Multiple calling hooks supplied mock results")

    result: Any = None
    exception: Exception | None = None
    if replacements:
        result = replacements[0].result
    else:
        try:
            result = await channel.adapter._call(channel, operation, **data)
        except Exception as error:
            exception = error

    if channel._called_hook:
        try:
            async with anyio.create_task_group() as tasks:
                for hook in tuple(channel._called_hook):
                    tasks.start_soon(
                        hook, channel, exception, operation, data, result
                    )
        except* MockCallException as errors:
            mocks = list(flatten_exception_group(errors))
            if len(mocks) > 1:
                logger.warning("Multiple hooks mocked the result; using first")
            result = mocks[0].result
            exception = None
        except* Exception as errors:
            for error in flatten_exception_group(errors):
                logger.error("Operation observer failed", exc_info=error)

    if exception is not None:
        raise exception
    return result
