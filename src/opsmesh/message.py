"""本模块定义了事件处理主要流程。

OpsMesh 内部处理并按优先级分发事件给所有事件响应器，提供了多个插槽以进行事件的预处理等。

FrontMatter:
    mdx:
        format: md
    sidebar_position: 2
    description: opsmesh.message 模块
"""

import contextlib
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anyio

from opsmesh.dependencies import Dependent
from opsmesh.exception import (
    IgnoredException,
    NoLogException,
    SkippedException,
    StopPropagation,
)
from opsmesh.internal.params import (
    ArgParam,
    DefaultParam,
    DependParam,
    EventParam,
    ExceptionParam,
    GatewayParam,
    MatcherParam,
    StateParam,
)
from opsmesh.log import logger
from opsmesh.matcher import Matcher, matchers
from opsmesh.rule import TrieRule
from opsmesh.typing import (
    T_DependencyCache,
    T_EventPostProcessor,
    T_EventPreProcessor,
    T_RunPostProcessor,
    T_RunPreProcessor,
    T_State,
)
from opsmesh.utils import (
    escape_tag,
    flatten_exception_group,
    run_coro_with_catch,
    run_coro_with_shield,
)

if TYPE_CHECKING:
    from opsmesh.adapters import Event, Gateway

_event_preprocessors: set[Dependent[Any]] = set()
_event_postprocessors: set[Dependent[Any]] = set()
_run_preprocessors: set[Dependent[Any]] = set()
_run_postprocessors: set[Dependent[Any]] = set()

EVENT_PCS_PARAMS = (
    DependParam,
    GatewayParam,
    EventParam,
    StateParam,
    DefaultParam,
)
RUN_PREPCS_PARAMS = (
    DependParam,
    GatewayParam,
    EventParam,
    StateParam,
    ArgParam,
    MatcherParam,
    DefaultParam,
)
RUN_POSTPCS_PARAMS = (
    DependParam,
    ExceptionParam,
    GatewayParam,
    EventParam,
    StateParam,
    ArgParam,
    MatcherParam,
    DefaultParam,
)


def event_preprocessor(func: T_EventPreProcessor) -> T_EventPreProcessor:
    """事件预处理。

    装饰一个函数，使它在每次接收到事件并分发给各响应器之前执行。
    """
    _event_preprocessors.add(
        Dependent[Any].parse(call=func, allow_types=EVENT_PCS_PARAMS)
    )
    return func


def event_postprocessor(func: T_EventPostProcessor) -> T_EventPostProcessor:
    """事件后处理。

    装饰一个函数，使它在每次接收到事件并分发给各响应器之后执行。
    """
    _event_postprocessors.add(
        Dependent[Any].parse(call=func, allow_types=EVENT_PCS_PARAMS)
    )
    return func


def run_preprocessor(func: T_RunPreProcessor) -> T_RunPreProcessor:
    """运行预处理。

    装饰一个函数，使它在每次事件响应器运行前执行。
    """
    _run_preprocessors.add(
        Dependent[Any].parse(call=func, allow_types=RUN_PREPCS_PARAMS)
    )
    return func


def run_postprocessor(func: T_RunPostProcessor) -> T_RunPostProcessor:
    """运行后处理。

    装饰一个函数，使它在每次事件响应器运行后执行。
    """
    _run_postprocessors.add(
        Dependent[Any].parse(call=func, allow_types=RUN_POSTPCS_PARAMS)
    )
    return func


def _handle_exception(exc_group: BaseExceptionGroup[Exception], msg: str) -> None:
    for exc in flatten_exception_group(exc_group):
        logger.opt(colors=True, exception=exc).error(msg)


async def _apply_event_preprocessors(
    gateway: "Gateway",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    show_log: bool = True,
) -> bool:
    """运行事件预处理。"""
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
                        gateway=gateway,
                        event=event,
                        state=state,
                        stack=stack,
                        dependency_cache=dependency_cache,
                    ),
                    (SkippedException,),
                )

        return True
    except* IgnoredException:
        logger.opt(colors=True).info(
            f"Event {escape_tag(event.get_event_name())} is <b>ignored</b>"
        )
    except* Exception as exc_group:
        _handle_exception(
            exc_group,
            "<r><bg #f8bbd0>Error when running EventPreProcessors. "
            "Event ignored!</bg #f8bbd0></r>",
        )

    return False


async def _apply_event_postprocessors(
    gateway: "Gateway",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    show_log: bool = True,
) -> None:
    """运行事件后处理。"""
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
                        gateway=gateway,
                        event=event,
                        state=state,
                        stack=stack,
                        dependency_cache=dependency_cache,
                    ),
                    (SkippedException,),
                )
    except* Exception as exc_group:
        _handle_exception(
            exc_group,
            "<r><bg #f8bbd0>Error when running EventPostProcessors</bg #f8bbd0></r>",
        )


async def _apply_run_preprocessors(
    gateway: "Gateway",
    event: "Event",
    state: T_State,
    matcher: Matcher,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
) -> bool:
    """运行事件响应器运行前处理。"""
    if not _run_preprocessors:
        return True

    with matcher.ensure_context(gateway, event):
        try:
            async with anyio.create_task_group() as tg:
                for proc in _run_preprocessors:
                    tg.start_soon(
                        run_coro_with_catch,
                        proc(
                            matcher=matcher,
                            gateway=gateway,
                            event=event,
                            state=state,
                            stack=stack,
                            dependency_cache=dependency_cache,
                        ),
                        (SkippedException,),
                    )

            return True
        except* IgnoredException:
            logger.opt(colors=True).info(f"{matcher} running is <b>cancelled</b>")
        except* Exception as exc_group:
            _handle_exception(
                exc_group,
                "<r><bg #f8bbd0>Error when running RunPreProcessors. "
                "Running cancelled!</bg #f8bbd0></r>",
            )

    return False


async def _apply_run_postprocessors(
    gateway: "Gateway",
    event: "Event",
    matcher: Matcher,
    exception: Exception | None = None,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
) -> None:
    """运行事件响应器运行后处理。"""
    if not _run_postprocessors:
        return

    with matcher.ensure_context(gateway, event):
        try:
            async with anyio.create_task_group() as tg:
                for proc in _run_postprocessors:
                    tg.start_soon(
                        run_coro_with_catch,
                        proc(
                            matcher=matcher,
                            exception=exception,
                            gateway=gateway,
                            event=event,
                            state=matcher.state,
                            stack=stack,
                            dependency_cache=dependency_cache,
                        ),
                        (SkippedException,),
                    )
        except* Exception as exc_group:
            _handle_exception(
                exc_group,
                "<r><bg #f8bbd0>Error when running RunPostProcessors</bg #f8bbd0></r>",
            )


async def _check_matcher(
    Matcher: type[Matcher],
    gateway: "Gateway",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
) -> bool:
    """检查事件响应器是否符合运行条件。"""
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
        if not await Matcher.check_perm(gateway, event, stack, dependency_cache):
            logger.trace(f"Permission conditions not met for {Matcher}")
            return False
    except Exception as e:
        logger.opt(colors=True, exception=e).error(
            f"<r><bg #f8bbd0>Permission check failed for {Matcher}.</bg #f8bbd0></r>"
        )
        return False

    try:
        if not await Matcher.check_rule(gateway, event, state, stack, dependency_cache):
            logger.trace(f"Rule conditions not met for {Matcher}")
            return False
    except Exception as e:
        logger.opt(colors=True, exception=e).error(
            f"<r><bg #f8bbd0>Rule check failed for {Matcher}.</bg #f8bbd0></r>"
        )
        return False

    return True


async def _run_matcher(
    Matcher: type[Matcher],
    gateway: "Gateway",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
) -> None:
    """运行事件响应器。"""
    logger.info(f"Event will be handled by {Matcher}")

    if Matcher.temp:
        with contextlib.suppress(Exception):
            Matcher.destroy()

    matcher = Matcher()

    if not await _apply_run_preprocessors(
        gateway=gateway,
        event=event,
        state=state,
        matcher=matcher,
        stack=stack,
        dependency_cache=dependency_cache,
    ):
        return

    exception = None

    logger.debug(f"Running {matcher}")

    try:
        await matcher.run(gateway, event, state, stack, dependency_cache)
    except Exception as e:
        logger.opt(colors=True, exception=e).error(
            f"<r><bg #f8bbd0>Running {matcher} failed.</bg #f8bbd0></r>"
        )
        exception = e

    await _apply_run_postprocessors(
        gateway=gateway,
        event=event,
        matcher=matcher,
        exception=exception,
        stack=stack,
        dependency_cache=dependency_cache,
    )

    if matcher.block:
        raise StopPropagation


async def check_and_run_matcher(
    Matcher: type[Matcher],
    gateway: "Gateway",
    event: "Event",
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
) -> None:
    """检查并运行事件响应器。"""
    if not await _check_matcher(
        Matcher=Matcher,
        gateway=gateway,
        event=event,
        state=state,
        stack=stack,
        dependency_cache=dependency_cache,
    ):
        return

    await _run_matcher(
        Matcher=Matcher,
        gateway=gateway,
        event=event,
        state=state,
        stack=stack,
        dependency_cache=dependency_cache,
    )


async def handle_event(gateway: "Gateway", event: "Event") -> None:
    """处理一个事件。调用该函数以实现分发事件。"""
    show_log = True
    log_msg = f"<m>{escape_tag(gateway.type)} {escape_tag(gateway.id)}</m> | "
    try:
        log_msg += event.get_log_string()
    except NoLogException:
        show_log = False
    if show_log:
        logger.opt(colors=True).success(log_msg)

    state: dict[Any, Any] = {}
    dependency_cache: T_DependencyCache = {}

    async with AsyncExitStack() as stack:
        if not await _apply_event_preprocessors(
            gateway=gateway,
            event=event,
            state=state,
            stack=stack,
            dependency_cache=dependency_cache,
        ):
            return

        # Trie Match
        try:
            TrieRule.get_value(gateway, event, state)
        except Exception as e:
            logger.opt(colors=True, exception=e).warning(
                "Error while parsing command for event"
            )

        break_flag = False

        for priority in sorted(matchers.keys()):
            if break_flag:
                break

            if show_log:
                logger.debug(f"Checking for matchers in priority {priority}...")

            if not (priority_matchers := matchers[priority]):
                continue

            try:
                async with anyio.create_task_group() as tg:
                    for matcher in priority_matchers:
                        tg.start_soon(
                            run_coro_with_shield,
                            check_and_run_matcher(
                                matcher,
                                gateway,
                                event,
                                state.copy(),
                                stack,
                                dependency_cache,
                            ),
                        )
            except* StopPropagation:
                break_flag = True
                logger.debug("Stop event propagation")
            except* Exception as exc_group:
                _handle_exception(
                    exc_group,
                    "<r><bg #f8bbd0>Error when checking Matcher.</bg #f8bbd0></r>",
                )

        if show_log:
            logger.debug("Checking for matchers completed")

        await _apply_event_postprocessors(
            gateway, event, state, stack, dependency_cache
        )
