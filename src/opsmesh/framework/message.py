"""One event dispatch chain for bots, timers, and domain event notifications."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .adapters import OpsMeshBot, OpsMeshEvent
from .dependencies import CallableDependency, DependencyScope
from .events import DispatchContext, bind_context
from .exception import MatcherSkipped, MatcherStopPropagation
from .matcher import MatcherManager, OpsMeshMatcher, matchers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventProcessor:
    dependency: CallableDependency
    enabled: Callable[[], bool]


_preprocessors: list[EventProcessor] = []
_postprocessors: list[EventProcessor] = []


@dataclass
class DispatchResult:
    event_id: str
    matched: int = 0
    stopped: bool = False
    errors: list[Exception] = field(default_factory=list)


def _register_processor(
    fn: Callable[..., Any], collection: list[EventProcessor]
) -> Callable[..., Any]:
    from .plugin import get_current_plugin

    owner = get_current_plugin()
    processor = EventProcessor(CallableDependency.parse(fn), lambda: owner is None or owner.enabled)
    collection.append(processor)
    if owner:
        owner.own(lambda: collection.remove(processor) if processor in collection else None)
    return fn


def event_preprocessor(fn: Callable[..., Any]) -> Callable[..., Any]:
    return _register_processor(fn, _preprocessors)


def event_postprocessor(fn: Callable[..., Any]) -> Callable[..., Any]:
    return _register_processor(fn, _postprocessors)


async def handle_event(
    event: DispatchContext[OpsMeshEvent] | OpsMeshEvent,
    bot: OpsMeshBot | None = None,
    *,
    registry: MatcherManager | None = None,
    raise_on_error: bool = False,
) -> DispatchResult:
    context = event if isinstance(event, DispatchContext) else DispatchContext(event=event, bot=bot)
    result = DispatchResult(context.event.id)
    values = {
        **context.values,
        "event": context.event,
        "bot": context.bot,
        "context": context,
        "state": {},
        "result": result,
    }
    with bind_context(context):
        async with DependencyScope(values) as scope:
            try:
                for processor in tuple(_preprocessors):
                    if processor.enabled():
                        await scope.invoke(processor.dependency)
            except MatcherSkipped:
                return result
            snapshot = (registry or matchers).snapshot()
            for priority in sorted({m.priority for m in snapshot}):
                group = [m for m in snapshot if m.priority == priority]

                async def run(matcher: OpsMeshMatcher) -> None:
                    try:
                        if await matcher.run(context, scope):
                            result.matched += 1
                            if matcher.block:
                                result.stopped = True
                    except MatcherStopPropagation:
                        result.stopped = True
                        result.matched += 1
                    except Exception as exc:
                        result.errors.append(exc)
                        logger.exception("OpsMeshMatcher failed for event %s", context.event.id)

                # All same-priority matchers finish; block only stops later priorities.
                await asyncio.gather(*(run(m) for m in group))
                if result.stopped:
                    break
            for processor in tuple(_postprocessors):
                try:
                    if processor.enabled():
                        await scope.invoke(processor.dependency)
                except Exception as exc:
                    result.errors.append(exc)
            if raise_on_error and result.errors:
                raise ExceptionGroup("OpsMeshEvent processing failed", result.errors)
    return result
