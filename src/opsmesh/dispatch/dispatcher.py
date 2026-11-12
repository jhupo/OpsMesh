"""Dispatch incoming events by matcher priority within a resource scope."""

import logging
from collections.abc import Callable
from contextlib import AsyncExitStack, nullcontext
from typing import TYPE_CHECKING, Any

import anyio

from opsmesh.exceptions import (
    NoLogException,
    StopPropagation,
)
from opsmesh.matchers import Matcher, matchers
from opsmesh.rules import TrieRule
from opsmesh.typing import (
    T_DependencyCache,
)

if TYPE_CHECKING:
    from opsmesh.adapters import Channel, Event

from .execution import check_and_run_matcher
from .processors import (
    _apply_event_postprocessors,
    _apply_event_preprocessors,
    _handle_exception,
)
from .results import (
    DispatchResult,
    collect_errors,
    collect_progress,
    record_error,
    record_outcome,
)

logger = logging.getLogger(__name__)


class _DispatchResources(AsyncExitStack):
    """Allow dependency finalizers to await after event cancellation."""

    async def __aexit__(self, exc_type, exc_value, traceback):
        with anyio.CancelScope(shield=True):
            return await super().__aexit__(exc_type, exc_value, traceback)


async def _dispatch_event(
    channel: "Channel",
    event: "Event",
    invocation_context: Any = None,
    *,
    stack: AsyncExitStack | None = None,
    matcher_filter: Callable[[type[Matcher]], bool] | None = None,
) -> None:
    """Dispatch an incoming event through the processing pipeline.

    Args:
        channel: The channel instance.
        event: The incoming event instance.
        invocation_context: Trusted context supplied by the dispatch owner.
        stack: Optional resources retained by the transport until response end.
        matcher_filter: Optional eligibility restriction applied before checks.

    Examples:
        ```python
        driver.task_group.start_soon(handle_event, channel, event)
        ```
    """
    show_log = True
    log_msg = f"{channel.type} {channel.self_id} | "
    try:
        log_msg += event.get_log_string()
    except NoLogException:
        show_log = False
    if show_log:
        logger.info(log_msg)

    state: dict[Any, Any] = {}
    dependency_cache: T_DependencyCache = {}

    # create event scope context
    async with (
        nullcontext(stack) if stack is not None else _DispatchResources()
    ) as stack:
        if not await _apply_event_preprocessors(
            channel=channel,
            event=event,
            state=state,
            stack=stack,
            dependency_cache=dependency_cache,
            invocation_context=invocation_context,
        ):
            record_outcome("ignored")
            return

        # Trie Match
        try:
            TrieRule.get_value(channel, event, state)
        except Exception as e:
            record_error(e)
            logger.warning(
                "Error while parsing command for event",
                exc_info=(type(e), e, e.__traceback__),
            )

        break_flag = False

        async def run_matcher(matcher):
            nonlocal break_flag
            try:
                await check_and_run_matcher(
                    matcher,
                    channel,
                    event,
                    state.copy(),
                    stack,
                    dependency_cache,
                    invocation_context,
                )
            except* StopPropagation:
                break_flag = True
                logger.debug("Stop event propagation")
            except* Exception as exc_group:
                _handle_exception(exc_group, "Error when checking Matcher.")

        # iterate through all priority until stop propagation
        for priority in sorted(matchers.keys()):
            if break_flag:
                break

            if show_log:
                logger.debug(f"Checking for matchers in priority {priority}...")

            if not (priority_matchers := matchers[priority]):
                continue

            async with anyio.create_task_group() as tg:
                for matcher in tuple(priority_matchers):
                    if matcher_filter is None or matcher_filter(matcher):
                        tg.start_soon(run_matcher, matcher)

        if show_log:
            logger.debug("Checking for matchers completed")

        await _apply_event_postprocessors(
            channel, event, state, stack, dependency_cache, invocation_context
        )


async def dispatch_event(
    channel: "Channel",
    event: "Event",
    *,
    invocation_context: Any = None,
    stack: AsyncExitStack | None = None,
    matcher_filter: Callable[[type[Matcher]], bool] | None = None,
) -> DispatchResult:
    """Process one event and report caught failures after resource cleanup.

    Cancellation and resource-cleanup failures propagate immediately. Rules
    returning false and deliberately ignored events are not execution errors.
    An external stack transfers dependency cleanup to the transport owner,
    which must close it after the response finishes, including cancellation.
    A matcher filter narrows eligible handlers before their checks run.
    """
    with collect_errors() as errors, collect_progress() as progress:
        await _dispatch_event(
            channel,
            event,
            invocation_context,
            stack=stack,
            matcher_filter=matcher_filter,
        )
        return DispatchResult(
            tuple(errors),
            progress.matched,
            progress.handled,
            progress.suspended,
            progress.ignored,
        )


async def handle_event(
    channel: "Channel", event: "Event", *, invocation_context: Any = None
) -> None:
    """Process an event and propagate failures after resource cleanup.

    Delivery owners can pass this callable directly to an adapter or worker.
    Call dispatch_event instead when inspecting failures without raising.
    """
    result = await dispatch_event(
        channel, event, invocation_context=invocation_context
    )
    result.raise_for_errors()
