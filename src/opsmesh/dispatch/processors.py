"""Execute registered event and matcher lifecycle processors."""

import logging
from contextlib import AsyncExitStack
from typing import Any

import anyio

from opsmesh.events import Event
from opsmesh.exceptions import (
    IgnoredException,
    SkippedException,
)
from opsmesh.matchers import Matcher
from opsmesh.typing import (
    T_DependencyCache,
    T_State,
)
from opsmesh.utils import (
    flatten_exception_group,
    run_coro_with_catch,
)

from .hooks import (
    _event_postprocessors,
    _event_preprocessors,
    _run_postprocessors,
    _run_preprocessors,
)
from .results import record_error

logger = logging.getLogger(__name__)


def _handle_exception(
    exc_group: BaseExceptionGroup[Exception], msg: str
) -> None:
    for exc in flatten_exception_group(exc_group):
        record_error(exc)
        logger.error(
            msg,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


async def _apply_event_preprocessors(
    event: Event,
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
    show_log: bool = True,
) -> bool:
    """Execute event preprocessors.

    Args:
        event: The incoming event instance.
        state: The conversation state mapping.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.
        show_log: Whether to emit diagnostic logs.

    Returns:
        Whether event processing should continue.
    """
    if not _event_preprocessors:
        return True

    if show_log:
        logger.debug("Running PreProcessors...")

    try:
        async with anyio.create_task_group() as tg:
            for proc in _event_preprocessors:
                tg.start_soon(
                    run_coro_with_catch,
                    proc(
                        event=event,
                        state=state,
                        stack=stack,
                        dependency_cache=dependency_cache,
                        invocation_context=invocation_context,
                    ),
                    (SkippedException,),
                )

        return True
    except* IgnoredException:
        logger.info(f"Event {event.get_event_name()} is ignored")
    except* Exception as exc_group:
        _handle_exception(
            exc_group,
            "Error when running EventPreProcessors. Event ignored!",
        )

    return False


async def _apply_event_postprocessors(
    event: Event,
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
    show_log: bool = True,
) -> None:
    """Execute event postprocessors.

    Args:
        event: The incoming event instance.
        state: The conversation state mapping.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.
        show_log: Whether to emit diagnostic logs.
    """
    if not _event_postprocessors:
        return

    if show_log:
        logger.debug("Running PostProcessors...")

    try:
        async with anyio.create_task_group() as tg:
            for proc in _event_postprocessors:
                tg.start_soon(
                    run_coro_with_catch,
                    proc(
                        event=event,
                        state=state,
                        stack=stack,
                        dependency_cache=dependency_cache,
                        invocation_context=invocation_context,
                    ),
                    (SkippedException,),
                )
    except* Exception as exc_group:
        _handle_exception(
            exc_group,
            "Error when running EventPostProcessors",
        )


async def _apply_run_preprocessors(
    event: Event,
    state: T_State,
    matcher: Matcher,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
) -> bool:
    """Execute processors that run before a matcher.

    Args:
        event: The incoming event instance.
        state: The conversation state mapping.
        matcher: The matcher instance.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.

    Returns:
        Whether event processing should continue.
    """
    if not _run_preprocessors:
        return True

    # ensure matcher function can be correctly called
    with matcher.ensure_context(event):
        try:
            async with anyio.create_task_group() as tg:
                for proc in _run_preprocessors:
                    tg.start_soon(
                        run_coro_with_catch,
                        proc(
                            matcher=matcher,
                            event=event,
                            state=state,
                            stack=stack,
                            dependency_cache=dependency_cache,
                            invocation_context=invocation_context,
                        ),
                        (SkippedException,),
                    )

            return True
        except* IgnoredException:
            logger.info(f"{matcher} running is cancelled")
        except* Exception as exc_group:
            _handle_exception(
                exc_group,
                "Error when running RunPreProcessors. Running cancelled!",
            )

    return False


async def _apply_run_postprocessors(
    event: Event,
    matcher: Matcher,
    exception: Exception | None = None,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
) -> None:
    """Execute processors that run after a matcher.

    Args:
        event: The incoming event instance.
        matcher: The matcher instance.
        exception: The exception raised during matcher execution, if any.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.
    """
    if not _run_postprocessors:
        return

    with matcher.ensure_context(event):
        try:
            async with anyio.create_task_group() as tg:
                for proc in _run_postprocessors:
                    tg.start_soon(
                        run_coro_with_catch,
                        proc(
                            matcher=matcher,
                            exception=exception,
                            event=event,
                            state=matcher.state,
                            stack=stack,
                            dependency_cache=dependency_cache,
                            invocation_context=invocation_context,
                        ),
                        (SkippedException,),
                    )
        except* Exception as exc_group:
            _handle_exception(
                exc_group,
                "Error when running RunPostProcessors",
            )
