"""Per-invocation matcher state and explicit interaction control."""

from __future__ import annotations

import asyncio
import copy
import time
import weakref
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from opsmesh.framework.adapters import OpsMeshEvent, OpsMeshMessage
from opsmesh.framework.dependencies import CallableDependency, DependencyScope
from opsmesh.framework.events import DispatchContext
from opsmesh.framework.exception import (
    ContextUnavailableError,
    MatcherFinished,
    MatcherPaused,
    MatcherRejected,
    MatcherSkipped,
    MatcherStopPropagation,
    ReplyUnavailableError,
)
from opsmesh.framework.rule import Rule, as_rule

from .manager import matchers

if TYPE_CHECKING:
    from opsmesh.framework.plugin.model import OpsMeshPlugin


@dataclass
class MatcherInvocation:
    matcher: OpsMeshMatcher
    context: DispatchContext[OpsMeshEvent]
    state: dict[str, Any]


@dataclass
class PendingConversation:
    index: int
    state: dict[str, Any]
    expires: float


_current: ContextVar[MatcherInvocation] = ContextVar("opsmesh_matcher")


def get_current_matcher() -> OpsMeshMatcher:
    try:
        return _current.get().matcher
    except LookupError as exc:
        raise ContextUnavailableError("No active matcher") from exc


class OpsMeshMatcher:
    def __init__(
        self,
        *,
        event_type: str | type[OpsMeshEvent] | None = None,
        rule: Rule | Callable[..., Any] | None = None,
        permission: Rule | Callable[..., Any] | None = None,
        priority: int = 1,
        block: bool = False,
        temp: bool = False,
        state: dict[str, Any] | None = None,
        expire_seconds: float | None = None,
        conversation_timeout: float = 300,
        owner: OpsMeshPlugin | None = None,
        handlers: Iterable[Callable[..., Any] | CallableDependency] = (),
    ) -> None:
        self.event_type, self.rule, self.permission = event_type, as_rule(rule), as_rule(permission)
        self.priority, self.block, self.temp = priority, block, temp
        self.default_state = dict(state or {})
        self.expires = time.monotonic() + expire_seconds if expire_seconds is not None else None
        self.conversation_timeout = conversation_timeout
        self.owner = owner
        self.handlers = [
            h if isinstance(h, CallableDependency) else CallableDependency.parse(h)
            for h in handlers
        ]
        self._pending: dict[str, PendingConversation] = {}
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._temp_lock = asyncio.Lock()
        self._consumed = False

    @property
    def state(self) -> dict[str, Any]:
        try:
            invocation = _current.get()
        except LookupError as exc:
            raise ContextUnavailableError("No active matcher state") from exc
        if invocation.matcher is not self:
            raise ContextUnavailableError("This matcher is not executing")
        return invocation.state

    def handle(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers.append(CallableDependency.parse(fn))
            return fn

        return register

    def got(
        self, key: str, prompt: str | None = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        async def collect(event: OpsMeshEvent) -> None:
            if key not in self.state:
                marker = f"_awaiting_{key}"
                if self.state.pop(marker, False):
                    self.state[key] = event.get_message()
                else:
                    self.state[marker] = True
                    await self.reject(prompt)

        self.handlers.append(CallableDependency.parse(collect))
        return self.handle()

    async def send(self, message: str | OpsMeshMessage) -> Any:
        invocation = _current.get(None)
        if invocation is None or invocation.matcher is not self:
            raise ContextUnavailableError("This matcher is not executing")
        context = invocation.context
        if context.bot is None:
            raise ReplyUnavailableError("No bot; use an explicit platform delivery service")
        return await context.bot.send(
            context.reply_target or context.event,
            message if isinstance(message, OpsMeshMessage) else OpsMeshMessage(message),
        )

    async def finish(self, message: str | OpsMeshMessage | None = None) -> None:
        if message is not None:
            await self.send(message)
        raise MatcherFinished

    async def pause(self, message: str | OpsMeshMessage | None = None) -> None:
        self._require_session()
        if message is not None:
            await self.send(message)
        raise MatcherPaused

    async def reject(self, message: str | OpsMeshMessage | None = None) -> None:
        self._require_session()
        if message is not None:
            await self.send(message)
        raise MatcherRejected

    def _require_session(self) -> str:
        invocation = _current.get(None)
        if invocation is None or invocation.matcher is not self:
            raise ContextUnavailableError("This matcher is not executing")
        return invocation.context.event.get_session_id()

    def stop_propagation(self) -> None:
        raise MatcherStopPropagation

    async def run(self, context: DispatchContext[OpsMeshEvent], scope: DependencyScope) -> bool:
        now = time.monotonic()
        self._pending = {key: value for key, value in self._pending.items() if value.expires > now}
        if self.owner is not None and not self.owner.enabled:
            return False
        if self.expires is not None and self.expires <= time.monotonic():
            matchers.remove(self)
            return False
        try:
            session = context.event.get_session_id()
            bot_key = (
                f"{context.bot.type}:{context.bot.self_id}" if context.bot else context.event.source
            )
            session = f"{bot_key}:{session}"
        except ContextUnavailableError:
            session = None
        lock = (
            self._temp_lock
            if self.temp
            else (
                self._locks.setdefault(session, asyncio.Lock())
                if session is not None
                else asyncio.Lock()
            )
        )
        async with lock:
            if self._consumed:
                return False
            return await self._run(context, scope, session)

    async def _run(
        self,
        context: DispatchContext[OpsMeshEvent],
        scope: DependencyScope,
        session: str | None,
    ) -> bool:
        pending = self._pending.pop(session, None) if session else None
        if pending and pending.expires <= time.monotonic():
            pending = None
        state = pending.state if pending else copy.deepcopy(self.default_state)
        local = scope.fork(matcher=self, state=state)
        token = _current.set(MatcherInvocation(self, context, state))
        index = pending.index if pending else 0
        try:
            if not await self.permission(local):
                return False
            if not pending:
                if isinstance(self.event_type, str) and context.event.type != self.event_type:
                    return False
                if isinstance(self.event_type, type) and not isinstance(
                    context.event, self.event_type
                ):
                    return False
                if not await self.rule(local):
                    return False
            try:
                while index < len(self.handlers):
                    await local.invoke(self.handlers[index])
                    index += 1
            except MatcherFinished:
                pass
            except (MatcherPaused, MatcherRejected) as control:
                if session is None:
                    raise ContextUnavailableError(
                        "Interaction requires a stable session"
                    ) from control
                next_index = index + 1 if isinstance(control, MatcherPaused) else index
                self._pending[session] = PendingConversation(
                    next_index, state, time.monotonic() + self.conversation_timeout
                )
                return True
            if self.temp:
                self._consumed = True
                matchers.remove(self)
            return True
        except MatcherSkipped:
            return False
        finally:
            _current.reset(token)
