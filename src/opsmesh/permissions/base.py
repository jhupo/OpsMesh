"""Compose permission checks and bind access to event users."""

from contextlib import AsyncExitStack
from contextvars import ContextVar
from typing import Any, ClassVar, NoReturn, Self

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
)
from opsmesh.exceptions import SkippedException
from opsmesh.typing import T_DependencyCache, T_PermissionChecker
from opsmesh.utils import run_coro_with_catch

_permission_context: ContextVar[
    tuple[AsyncExitStack | None, T_DependencyCache | None, Any]
] = ContextVar("permission_context", default=(None, None, None))


class Permission:
    """A collection of permission checks for a matcher.

    Permissions are checked before the matcher executes an incoming event.

    Args:
        checkers: PermissionChecker

    Examples:
        ```python
        Permission(async_function) | sync_function
        # Equivalent to:
        Permission(async_function, sync_function)
        ```
    """

    __slots__ = ("checkers",)

    HANDLER_PARAM_TYPES: ClassVar[list[type[Param]]] = [
        ContextParam,
        DependParam,
        ChannelParam,
        EventParam,
        DefaultParam,
    ]

    def __init__(
        self, *checkers: T_PermissionChecker | Dependent[bool]
    ) -> None:
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
        """Registered permission checkers."""

    def __repr__(self) -> str:
        checkers = ", ".join(repr(checker) for checker in self.checkers)
        return f"Permission({checkers})"

    async def __call__(
        self,
        channel: Channel,
        event: Event,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ) -> bool:
        """Check whether any configured permission allows the event.

        Args:
            channel: The channel instance.
            event: The incoming event instance.
            stack: The asynchronous resource cleanup stack.
            dependency_cache: The event-scoped dependency cache.
            invocation_context: Trusted context supplied for this event only.
        """
        if not self.checkers:
            return True

        result = False

        async def _run_checker(checker: Dependent[bool]) -> None:
            nonlocal result
            # calculate the result first to avoid data racing
            is_passed = await run_coro_with_catch(
                checker(
                    channel=channel,
                    event=event,
                    stack=stack,
                    dependency_cache=dependency_cache,
                    invocation_context=invocation_context,
                ),
                (SkippedException,),
                False,
            )
            result |= is_passed

        token = _permission_context.set(
            (stack, dependency_cache, invocation_context)
        )
        try:
            async with anyio.create_task_group() as tg:
                for checker in self.checkers:
                    tg.start_soon(_run_checker, checker)
        finally:
            _permission_context.reset(token)

        return result

    def __and__(self, other: object) -> NoReturn:
        raise RuntimeError("And operation between Permissions is not allowed.")

    def __or__(
        self, other: "Permission | T_PermissionChecker | None"
    ) -> "Permission":
        if other is None:
            return self
        elif isinstance(other, Permission):
            return Permission(*self.checkers, *other.checkers)
        else:
            return Permission(*self.checkers, other)

    def __ror__(
        self, other: "Permission | T_PermissionChecker | None"
    ) -> "Permission":
        if other is None:
            return self
        elif isinstance(other, Permission):
            return Permission(*other.checkers, *self.checkers)
        else:
            return Permission(other, *self.checkers)


class User:
    """Check whether the current event belongs to an allowed conversation.

    Args:
        users: Allowed conversation identifiers.
        perm: Additional permissions that must also pass.
    """

    __slots__ = ("perm", "users")

    def __init__(
        self, users: tuple[str, ...], perm: Permission | None = None
    ) -> None:
        self.users = users
        self.perm = perm

    def __repr__(self) -> str:
        return (
            f"User(users={self.users}"
            + (f", permission={self.perm})" if self.perm else "")
            + ")"
        )

    async def __call__(self, channel: Channel, event: Event) -> bool:
        """Check the event user and any additional composed permission."""
        try:
            session = event.get_session_id()
        except Exception:
            return False
        if session not in self.users:
            return False
        if self.perm is None:
            return True
        stack, dependency_cache, invocation_context = _permission_context.get()
        return await self.perm(
            channel, event, stack, dependency_cache, invocation_context
        )

    @classmethod
    def _clean_permission(cls, perm: Permission) -> Permission | None:
        if len(perm.checkers) == 1 and isinstance(
            user_perm := next(iter(perm.checkers)).call, cls
        ):
            return user_perm.perm
        return perm

    @classmethod
    def from_event(cls, event: Event, perm: Permission | None = None) -> Self:
        """Derive a conversation permission from the current event.

        Replace existing conversation IDs when perm contains only User
        checkers.

        Args:
            event: The incoming event instance.
            perm: Additional permissions that must also pass.
        """
        return cls(
            (event.get_session_id(),), perm=perm and cls._clean_permission(perm)
        )

    @classmethod
    def from_permission(
        cls, *users: str, perm: Permission | None = None
    ) -> Self:
        """Restrict events to selected conversations and additional permissions.

        Replace existing conversation IDs when perm contains only User
        checkers.

        Args:
            users: Allowed conversation identifiers.
            perm: Additional permissions that must also pass.
        """
        return cls(users, perm=perm and cls._clean_permission(perm))


def USER(*users: str, perm: Permission | None = None):
    """Match events belonging to the selected conversations.

    Replace prior conversation IDs when perm contains only User checkers.

    Args:
        *users: Allowed conversation identifiers.
        perm: Additional permissions that must also pass.
    """
    return Permission(User.from_permission(*users, perm=perm))
