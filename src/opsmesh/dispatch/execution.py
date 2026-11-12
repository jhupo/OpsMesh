"""Check eligibility and execute individual matcher instances."""

import contextlib
import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opsmesh.exceptions import (
    StopPropagation,
)
from opsmesh.matchers import Matcher
from opsmesh.typing import (
    T_DependencyCache,
    T_State,
)

if TYPE_CHECKING:
    from opsmesh.adapters import Channel, Event

from .processors import _apply_run_postprocessors, _apply_run_preprocessors
from .results import record_error, record_outcome

logger = logging.getLogger(__name__)


async def _check_matcher(
    Matcher: type[Matcher],
    channel: "Channel",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
) -> bool:
    """Check whether a matcher can process the event.

    Remove expired matchers; otherwise check event type, permission, and
    rules.

    Args:
        Matcher: The matcher class to check.
        channel: The channel instance.
        event: The incoming event instance.
        state: The conversation state mapping.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.

    Returns:
        Whether the matcher is eligible to run.
    """
    if Matcher.expire_time:
        expire_time = Matcher.expire_time
        if expire_time.utcoffset() is not None:
            expire_time = expire_time.astimezone(UTC)
            now = datetime.now(UTC)
        else:
            now = datetime.now()

        if now > expire_time:
            with contextlib.suppress(Exception):
                Matcher.destroy()
            return False

    try:
        if not await Matcher.check_perm(
            channel, event, stack, dependency_cache, invocation_context
        ):
            logger.debug(f"Permission conditions not met for {Matcher}")
            return False
    except Exception as e:
        record_error(e)
        logger.error(
            f"Permission check failed for {Matcher}.",
            exc_info=(type(e), e, e.__traceback__),
        )
        return False

    try:
        if not await Matcher.check_rule(
            channel, event, state, stack, dependency_cache, invocation_context
        ):
            logger.debug(f"Rule conditions not met for {Matcher}")
            return False
    except Exception as e:
        record_error(e)
        logger.error(
            f"Rule check failed for {Matcher}.",
            exc_info=(type(e), e, e.__traceback__),
        )
        return False

    return True


async def _run_matcher(
    Matcher: type[Matcher],
    channel: "Channel",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
) -> None:
    """Execute a matcher for the event.

    Temporary matcher classes are removed from the registry before execution.

    Args:
        Matcher: The matcher class.
        channel: The channel instance.
        event: The incoming event instance.
        state: The conversation state mapping.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.

    Raises:
        StopPropagation: The matcher blocks further event propagation.
    """
    logger.info(f"Event will be handled by {Matcher}")

    record_outcome("matched")
    if Matcher.temp:
        with contextlib.suppress(Exception):
            Matcher.destroy()

    matcher = Matcher()

    if not await _apply_run_preprocessors(
        channel=channel,
        event=event,
        state=state,
        matcher=matcher,
        stack=stack,
        dependency_cache=dependency_cache,
        invocation_context=invocation_context,
    ):
        record_outcome("ignored")
        return

    exception = None

    logger.debug(f"Running {matcher}")

    try:
        await matcher.run(
            channel, event, state, stack, dependency_cache, invocation_context
        )
        record_outcome("suspended" if matcher.suspended else "handled")
    except Exception as e:
        record_error(e)
        logger.error(
            f"Running {matcher} failed.",
            exc_info=(type(e), e, e.__traceback__),
        )
        exception = e

    await _apply_run_postprocessors(
        channel=channel,
        event=event,
        matcher=matcher,
        exception=exception,
        stack=stack,
        dependency_cache=dependency_cache,
        invocation_context=invocation_context,
    )

    if matcher.block:
        raise StopPropagation


async def check_and_run_matcher(
    Matcher: type[Matcher],
    channel: "Channel",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    invocation_context: Any = None,
) -> None:
    """Check a matcher and execute it if eligible.

    Args:
        Matcher: The matcher class.
        channel: The channel instance.
        event: The incoming event instance.
        state: The conversation state mapping.
        stack: The asynchronous resource cleanup stack.
        dependency_cache: The event-scoped dependency cache.
        invocation_context: Trusted context supplied for this event only.
    """
    if not await _check_matcher(
        Matcher=Matcher,
        channel=channel,
        event=event,
        state=state,
        stack=stack,
        dependency_cache=dependency_cache,
        invocation_context=invocation_context,
    ):
        return

    await _run_matcher(
        Matcher=Matcher,
        channel=channel,
        event=event,
        state=state,
        stack=stack,
        dependency_cache=dependency_cache,
        invocation_context=invocation_context,
    )
