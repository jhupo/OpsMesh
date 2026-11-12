"""Compose rule predicates for event matching."""

from contextlib import AsyncExitStack
from typing import Any, ClassVar, NoReturn

import anyio

from opsmesh.adapters import Channel, Event
from opsmesh.dependencies import Dependent
from opsmesh.dependencies.context import ContextParam
from opsmesh.dependencies.params import (
    ChannelParam,
    DefaultParam,
    DependParam,
    EventParam,
    Param,
    StateParam,
)
from opsmesh.exceptions import SkippedException
from opsmesh.typing import T_DependencyCache, T_RuleChecker, T_State


class Rule:
    """A collection of rule checks for a matcher.

    Permissions are checked before the matcher executes an incoming event.

    Args:
        *checkers: RuleChecker

    Examples:
        ```python
        Rule(async_function) & sync_function
        # Equivalent to:
        Rule(async_function, sync_function)
        ```
    """

    __slots__ = ("checkers",)

    HANDLER_PARAM_TYPES: ClassVar[list[type[Param]]] = [
        ContextParam,
        DependParam,
        ChannelParam,
        EventParam,
        StateParam,
        DefaultParam,
    ]

    def __init__(self, *checkers: T_RuleChecker | Dependent[bool]) -> None:
        self.checkers: set[Dependent[bool]] = {
            (
                checker
                if isinstance(checker, Dependent)
                else Dependent[bool].parse(
                    call=checker, allow_types=self.HANDLER_PARAM_TYPES
                )
            )
            for checker in checkers
        }
        """Registered rule checkers."""

    def __repr__(self) -> str:
        return f"Rule({', '.join(repr(checker) for checker in self.checkers)})"

    async def __call__(
        self,
        channel: Channel,
        event: Event,
        state: T_State,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ) -> bool:
        """Check whether every configured rule matches.

        Args:
            channel: The channel instance.
            event: The incoming event instance.
            state: The current event-processing state.
            stack: The asynchronous resource cleanup stack.
            dependency_cache: The event-scoped dependency cache.
            invocation_context: Trusted context supplied for this event only.
        """
        if not self.checkers:
            return True

        result = True

        async def _run_checker(checker: Dependent[bool]) -> None:
            nonlocal result
            # calculate the result first to avoid data racing
            is_passed = await checker(
                channel=channel,
                event=event,
                state=state,
                stack=stack,
                dependency_cache=dependency_cache,
                invocation_context=invocation_context,
            )
            result &= is_passed

        try:
            async with anyio.create_task_group() as tg:
                for checker in self.checkers:
                    tg.start_soon(_run_checker, checker)
        except* SkippedException:
            result = False

        return result

    def __and__(self, other: "Rule | T_RuleChecker | None") -> "Rule":
        if other is None:
            return self
        elif isinstance(other, Rule):
            return Rule(*self.checkers, *other.checkers)
        else:
            return Rule(*self.checkers, other)

    def __rand__(self, other: "Rule | T_RuleChecker | None") -> "Rule":
        if other is None:
            return self
        elif isinstance(other, Rule):
            return Rule(*other.checkers, *self.checkers)
        else:
            return Rule(other, *self.checkers)

    def __or__(self, other: object) -> NoReturn:
        raise RuntimeError("Or operation between rules is not allowed.")
