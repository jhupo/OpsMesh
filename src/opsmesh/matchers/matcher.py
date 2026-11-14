"""Matcher execution, handler state, and conversation control."""

import inspect
import logging
import sys
import warnings
from collections.abc import Iterable
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import (  # noqa: UP035
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    NoReturn,
    Self,
    Type,
    TypeVar,
    overload,
)

from opsmesh.adapters import (
    Channel,
    Event,
    Message,
    MessageSegment,
    MessageTemplate,
)
from opsmesh.consts import (
    ARG_KEY,
    LAST_RECEIVE_KEY,
    PAUSE_PROMPT_RESULT_KEY,
    RECEIVE_KEY,
    REJECT_CACHE_TARGET,
    REJECT_PROMPT_RESULT_KEY,
    REJECT_TARGET,
)
from opsmesh.dependencies import Dependent, Param
from opsmesh.dependencies.context import ContextParam
from opsmesh.dependencies.params import (
    ArgParam,
    ChannelParam,
    DefaultParam,
    DependParam,
    Depends,
    EventParam,
    MatcherParam,
    StateParam,
)
from opsmesh.exceptions import (
    FinishedException,
    PausedException,
    RejectedException,
    SkippedException,
    StopPropagation,
)
from opsmesh.permissions.base import Permission, User
from opsmesh.rules.base import Rule
from opsmesh.typing import (
    T_DependencyCache,
    T_Handler,
    T_PermissionUpdater,
    T_State,
    T_TypeUpdater,
)
from opsmesh.utils import classproperty, flatten_exception_group

from .registry import matchers

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from opsmesh.plugins import Plugin

T = TypeVar("T")

current_channel: ContextVar[Channel] = ContextVar("current_channel")
current_event: ContextVar[Event] = ContextVar("current_event")
current_matcher: ContextVar["Matcher"] = ContextVar("current_matcher")
current_handler: ContextVar[Dependent[Any]] = ContextVar("current_handler")


@dataclass
class MatcherSource:
    """Source location and plugin context for a matcher."""

    plugin_id: str | None = None
    """The identifier of the plugin that owns this matcher."""
    module_name: str | None = None
    """The module path containing this matcher."""
    lineno: int | None = None
    """The source line where this matcher was defined."""

    @property
    def plugin(self) -> "Plugin | None":
        """The plugin that owns this matcher."""
        from opsmesh.plugins.registry import get_plugin

        if self.plugin_id is not None:
            return get_plugin(self.plugin_id)

    @property
    def plugin_name(self) -> str | None:
        """The name of the plugin that owns this matcher."""
        return self.plugin and self.plugin.name

    @property
    def module(self) -> ModuleType | None:
        """The loaded module that originally registered this matcher."""
        if self.module_name is not None:
            return sys.modules.get(self.module_name)

    @property
    def file(self) -> Path | None:
        """The source path of the registering module, when available."""
        if self.module is not None and (
            file := inspect.getsourcefile(self.module)
        ):
            return Path(file).absolute()


class MatcherMeta(type):
    """Provide source-aware representations for matcher classes."""

    if TYPE_CHECKING:
        type: str
        _source: MatcherSource | None
        module_name: str | None

    def __repr__(self) -> str:
        return (
            f"{self.__name__}(type={self.type!r}"
            + (f", module={self.module_name}" if self.module_name else "")
            + (
                f", lineno={self._source.lineno}"
                if self._source and self._source.lineno is not None
                else ""
            )
            + ")"
        )


class Matcher(metaclass=MatcherMeta):
    """An event matcher and its handler execution state."""

    _source: ClassVar[MatcherSource | None] = None
    _origin: ClassVar[type["Matcher"] | None] = None

    type: ClassVar[str] = ""
    """The event type matched by this matcher."""
    rule: ClassVar[Rule] = Rule()
    """The matcher's rule checks."""
    permission: ClassVar[Permission] = Permission()
    """The matcher's permission checks."""
    handlers: ClassVar[list[Dependent[Any]]] = []
    """The event handlers registered on this matcher."""
    priority: ClassVar[int] = 1
    """The matcher's dispatch priority."""
    block: bool = False
    """Whether this matcher blocks lower-priority matchers."""
    temp: ClassVar[bool] = False
    """Whether this matcher is removed after its first use."""
    expire_time: ClassVar[datetime | None] = None
    """The matcher's expiration timestamp."""

    _default_state: ClassVar[T_State] = {}
    """The matcher's default state."""

    _default_type_updater: ClassVar[Dependent[str] | None] = None
    """The callback that updates the accepted event type."""
    _default_permission_updater: ClassVar[Dependent[Permission] | None] = None
    """The callback that updates session permissions."""

    HANDLER_PARAM_TYPES: ClassVar[tuple[Type[Param], ...]] = (  # noqa: UP006
        ContextParam,
        DependParam,
        ChannelParam,
        EventParam,
        StateParam,
        ArgParam,
        MatcherParam,
        DefaultParam,
    )

    def __init__(self):
        self.remain_handlers: list[Dependent[Any]] = self.handlers.copy()
        self.state = self._default_state.copy()
        self.suspended = False

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(type={self.type!r}"
            + (f", module={self.module_name}" if self.module_name else "")
            + (
                f", lineno={self._source.lineno}"
                if self._source and self._source.lineno is not None
                else ""
            )
            + ")"
        )

    @classmethod
    def new(
        cls,
        type_: str = "",
        rule: Rule | None = None,
        permission: Permission | None = None,
        handlers: list[T_Handler | Dependent[Any]] | None = None,
        temp: bool = False,
        priority: int = 1,
        block: bool = False,
        *,
        plugin: "Plugin | None" = None,
        module: ModuleType | None = None,
        source: MatcherSource | None = None,
        expire_time: datetime | timedelta | None = None,
        default_state: T_State | None = None,
        default_type_updater: T_TypeUpdater | Dependent[str] | None = None,
        default_permission_updater: T_PermissionUpdater
        | Dependent[Permission]
        | None = None,
    ) -> Type[Self]:  # noqa: UP006
        """Create and register a matcher class.

        Args:
            type_: The event type; an empty string accepts any event type.
            rule: The matching rule.
            permission: The required permission.
            handlers: The event handler sequence.
            temp: Whether to remove this matcher after one execution.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block propagation to lower-priority matchers.
            plugin: Deprecated. The plugin that owns this matcher.
            module: Deprecated. The module that owns this matcher.
            source: The matcher's source location and plugin context.
            expire_time: The deadline after which this matcher is removed on
                checking.
            default_state: The initial state mapping.
            default_type_updater: The default event-type update callback.
            default_permission_updater: The default session-permission update
                callback.

        Returns:
            The newly created matcher class.
        """
        if plugin is not None:
            warnings.warn(
                (
                    "Pass `plugin` context info to "
                    "create Matcher is deprecated. "
                    "Use `source` instead."
                ),
                DeprecationWarning,
                stacklevel=2,
            )
        if module is not None:
            warnings.warn(
                (
                    "Pass `module` context info to "
                    "create Matcher is deprecated. "
                    "Use `source` instead."
                ),
                DeprecationWarning,
                stacklevel=2,
            )
        source = source or (
            MatcherSource(
                plugin_id=plugin and plugin.id_,
                module_name=module and module.__name__,
            )
            if plugin is not None or module is not None
            else None
        )

        if (
            isinstance(expire_time, datetime)
            and expire_time.utcoffset() is not None
        ):
            expire_time = expire_time.astimezone(UTC)

        NewMatcher = type(
            cls.__name__,
            (cls,),
            {
                "_source": source,
                "_origin": cls.origin if temp and cls is not Matcher else None,
                "type": type_,
                "rule": rule or Rule(),
                "permission": permission or Permission(),
                "handlers": (
                    [
                        (
                            handler
                            if isinstance(handler, Dependent)
                            else Dependent[Any].parse(
                                call=handler,
                                allow_types=cls.HANDLER_PARAM_TYPES,
                            )
                        )
                        for handler in handlers
                    ]
                    if handlers
                    else []
                ),
                "temp": temp,
                "expire_time": (
                    expire_time
                    and (
                        expire_time
                        if isinstance(expire_time, datetime)
                        else datetime.now() + expire_time
                    )
                ),
                "priority": priority,
                "block": block,
                "_default_state": default_state or {},
                "_default_type_updater": (
                    default_type_updater
                    and (
                        default_type_updater
                        if isinstance(default_type_updater, Dependent)
                        else Dependent[str].parse(
                            call=default_type_updater,
                            allow_types=cls.HANDLER_PARAM_TYPES,
                        )
                    )
                ),
                "_default_permission_updater": (
                    default_permission_updater
                    and (
                        default_permission_updater
                        if isinstance(default_permission_updater, Dependent)
                        else Dependent[Permission].parse(
                            call=default_permission_updater,
                            allow_types=cls.HANDLER_PARAM_TYPES,
                        )
                    )
                ),
            },
        )

        logger.debug(f"Define new matcher {NewMatcher}")

        matchers[priority].append(NewMatcher)
        from opsmesh.plugins.registry import track_registration

        def remove():
            if NewMatcher in matchers.get(priority, []):
                matchers[priority].remove(NewMatcher)

        NewMatcher._release_registration = staticmethod(
            track_registration(remove, NewMatcher.module)
        )

        return NewMatcher  # type: ignore

    @classmethod
    def destroy(cls) -> None:
        """Remove this matcher class from the registry."""
        matchers[cls.priority].remove(cls)
        if release := cls.__dict__.get("_release_registration"):
            release.__func__()

    @classproperty
    def origin(cls) -> Type["Matcher"]:  # noqa: UP006
        """The original matcher that owns this continuation, or itself."""
        return cls._origin or cls

    @classproperty
    def plugin(cls) -> "Plugin | None":
        """The plugin that owns this matcher."""
        return cls._source and cls._source.plugin

    @classproperty
    def plugin_id(cls) -> str | None:
        """The identifier of the plugin that owns this matcher."""
        return cls._source and cls._source.plugin_id

    @classproperty
    def plugin_name(cls) -> str | None:
        """The name of the plugin that owns this matcher."""
        return cls._source and cls._source.plugin_name

    @classproperty
    def module(cls) -> ModuleType | None:
        """The plugin module that contains this matcher."""
        return cls._source and cls._source.module

    @classproperty
    def module_name(cls) -> str | None:
        """The plugin module path for this matcher."""
        return cls._source and cls._source.module_name

    @classmethod
    async def check_perm(
        cls,
        channel: Channel,
        event: Event,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ) -> bool:
        """Check whether the event satisfies the matcher permissions.

        Args:
            channel: The channel instance.
            event: The incoming event.
            stack: The asynchronous resource cleanup stack.
            dependency_cache: The event-scoped dependency cache.
            invocation_context: Trusted context supplied for this event only.

        Returns:
            Whether the permission checks passed.
        """
        event_type = event.get_type()
        return event_type == (cls.type or event_type) and await cls.permission(
            channel, event, stack, dependency_cache, invocation_context
        )

    @classmethod
    async def check_rule(
        cls,
        channel: Channel,
        event: Event,
        state: T_State,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ) -> bool:
        """Check whether the event satisfies the matcher rules.

        Args:
            channel: The channel instance.
            event: The incoming event.
            state: The current state mapping.
            stack: The asynchronous resource cleanup stack.
            dependency_cache: The event-scoped dependency cache.
            invocation_context: Trusted context supplied for this event only.

        Returns:
            Whether the rule checks passed.
        """
        event_type = event.get_type()
        return event_type == (cls.type or event_type) and await cls.rule(
            channel, event, state, stack, dependency_cache, invocation_context
        )

    @classmethod
    def type_updater(cls, func: T_TypeUpdater) -> T_TypeUpdater:
        """Register the default event-type update callback.

        Args:
            func: The event-type update callback.
        """
        cls._default_type_updater = Dependent[str].parse(
            call=func, allow_types=cls.HANDLER_PARAM_TYPES
        )
        return func

    @classmethod
    def permission_updater(
        cls, func: T_PermissionUpdater
    ) -> T_PermissionUpdater:
        """Register the default session-permission update callback.

        Args:
            func: The session-permission update callback.
        """
        cls._default_permission_updater = Dependent[Permission].parse(
            call=func, allow_types=cls.HANDLER_PARAM_TYPES
        )
        return func

    @classmethod
    def append_handler(
        cls, handler: T_Handler, parameterless: Iterable[Any] | None = None
    ) -> Dependent[Any]:
        """Parse and append a handler with its parameterless dependencies."""
        handler_ = Dependent[Any].parse(
            call=handler,
            parameterless=parameterless,
            allow_types=cls.HANDLER_PARAM_TYPES,
        )
        cls.handlers.append(handler_)
        return handler_

    @classmethod
    def handle(
        cls, parameterless: Iterable[Any] | None = None
    ) -> Callable[[T_Handler], T_Handler]:
        """Register a handler on this matcher.

        Args:
            parameterless: Dependencies evaluated without binding a handler
                parameter.
        """

        def _decorator(func: T_Handler) -> T_Handler:
            cls.append_handler(func, parameterless=parameterless)
            return func

        return _decorator

    @classmethod
    def receive(
        cls, id: str = "", parameterless: Iterable[Any] | None = None
    ) -> Callable[[T_Handler], T_Handler]:
        """Run the decorated handler after receiving a new user message.

        Args:
            id: The receive identifier.
            parameterless: Dependencies evaluated without binding a handler
                parameter.
        """

        async def _receive(event: Event, matcher: "Matcher") -> None:
            matcher.set_target(RECEIVE_KEY.format(id=id))
            if matcher.get_target() == RECEIVE_KEY.format(id=id):
                matcher.set_receive(id, event)
                return
            if matcher.get_receive(id, ...) is not ...:
                return
            await matcher.reject()

        _parameterless = (Depends(_receive), *(parameterless or ()))

        def _decorator(func: T_Handler) -> T_Handler:
            if cls.handlers and cls.handlers[-1].call is func:
                func_handler = cls.handlers[-1]
                new_handler = Dependent(
                    call=func_handler.call,
                    params=func_handler.params,
                    parameterless=Dependent.parse_parameterless(
                        tuple(_parameterless), cls.HANDLER_PARAM_TYPES
                    )
                    + func_handler.parameterless,
                )
                cls.handlers[-1] = new_handler
            else:
                cls.append_handler(func, parameterless=_parameterless)

            return func

        return _decorator

    @classmethod
    def got(
        cls,
        key: str,
        prompt: str | Message | MessageSegment | MessageTemplate | None = None,
        parameterless: Iterable[Any] | None = None,
    ) -> Callable[[T_Handler], T_Handler]:
        """Collect an argument named key before running the decorated handler.

        If key is missing, wait for another user message before running the
        handler.
        If key is already present, continue immediately.

        Args:
            key: The argument name.
            prompt: The message sent when the argument is missing.
            parameterless: Dependencies evaluated without binding a handler
                parameter.
        """

        async def _key_getter(event: Event, matcher: "Matcher"):
            matcher.set_target(ARG_KEY.format(key=key))
            if matcher.get_target() == ARG_KEY.format(key=key):
                matcher.set_arg(key, event.get_message())
                return
            if matcher.get_arg(key, ...) is not ...:
                return
            await matcher.reject(prompt)

        _parameterless = (Depends(_key_getter), *(parameterless or ()))

        def _decorator(func: T_Handler) -> T_Handler:
            if cls.handlers and cls.handlers[-1].call is func:
                func_handler = cls.handlers[-1]
                new_handler = Dependent(
                    call=func_handler.call,
                    params=func_handler.params,
                    parameterless=Dependent.parse_parameterless(
                        tuple(_parameterless), cls.HANDLER_PARAM_TYPES
                    )
                    + func_handler.parameterless,
                )
                cls.handlers[-1] = new_handler
            else:
                cls.append_handler(func, parameterless=_parameterless)

            return func

        return _decorator

    @classmethod
    async def send(
        cls,
        message: str | Message | MessageSegment | MessageTemplate,
        **kwargs: Any,
    ) -> Any:
        """Send a message to the user in the current interaction.

        Args:
            message: Initial message content.
            kwargs: Arguments forwarded to Channel.send;
                consult the adapter's channel operation documentation.
        """
        channel = current_channel.get()
        event = current_event.get()
        if isinstance(message, MessageTemplate):
            state = current_matcher.get().state
            _message = message.format(**state)
        else:
            _message = message
        return await channel.send(event=event, message=_message, **kwargs)

    @classmethod
    async def finish(
        cls,
        message: str | Message | MessageSegment | MessageTemplate | None = None,
        **kwargs,
    ) -> NoReturn:
        """Send a message and finish the current matcher.

        Args:
            message: Initial message content.
            kwargs: Arguments forwarded to Channel.send;
                consult the adapter's channel operation documentation.
        """
        if message is not None:
            await cls.send(message, **kwargs)
        raise FinishedException

    @classmethod
    async def pause(
        cls,
        prompt: str | Message | MessageSegment | MessageTemplate | None = None,
        **kwargs,
    ) -> NoReturn:
        """Send a prompt and resume with the next handler after another message.

        Args:
            prompt: The prompt message.
            kwargs: Arguments forwarded to Channel.send;
                consult the adapter's channel operation documentation.
        """
        try:
            matcher = current_matcher.get()
        except Exception:
            matcher = None

        if prompt is not None:
            result = await cls.send(prompt, **kwargs)
            if matcher is not None:
                matcher.state[PAUSE_PROMPT_RESULT_KEY] = result
        raise PausedException

    @classmethod
    async def reject(
        cls,
        prompt: str | Message | MessageSegment | MessageTemplate | None = None,
        **kwargs,
    ) -> NoReturn:
        """Reject the most recently collected argument or received event.

        Send a prompt, suspend processing, and rerun this handler on the next
        event.

        Args:
            prompt: The prompt message.
            kwargs: Arguments forwarded to Channel.send;
                consult the adapter's channel operation documentation.
        """
        try:
            matcher = current_matcher.get()
            key = matcher.get_target()
        except Exception:
            matcher = None
            key = None

        key = (
            REJECT_PROMPT_RESULT_KEY.format(key=key)
            if key is not None
            else None
        )

        if prompt is not None:
            result = await cls.send(prompt, **kwargs)
            if key is not None and matcher:
                matcher.state[key] = result
        raise RejectedException

    @classmethod
    async def reject_arg(
        cls,
        key: str,
        prompt: str | Message | MessageSegment | MessageTemplate | None = None,
        **kwargs,
    ) -> NoReturn:
        """Reject the most recently collected argument.

        Send a prompt, suspend processing, and rerun this handler on the next
        message.

        Args:
            key: The argument name.
            prompt: The prompt message.
            kwargs: Arguments forwarded to Channel.send;
                consult the adapter's channel operation documentation.
        """
        matcher = current_matcher.get()
        arg_key = ARG_KEY.format(key=key)
        matcher.set_target(arg_key)

        if prompt is not None:
            result = await cls.send(prompt, **kwargs)
            matcher.state[REJECT_PROMPT_RESULT_KEY.format(key=arg_key)] = result
        raise RejectedException

    @classmethod
    async def reject_receive(
        cls,
        id: str = "",
        prompt: str | Message | MessageSegment | MessageTemplate | None = None,
        **kwargs,
    ) -> NoReturn:
        """Reject the most recently received event.

        Send a prompt, suspend processing, and rerun this handler on the next
        event.

        Args:
            id: The receive identifier.
            prompt: The prompt message.
            kwargs: Arguments forwarded to Channel.send;
                consult the adapter's channel operation documentation.
        """
        matcher = current_matcher.get()
        receive_key = RECEIVE_KEY.format(id=id)
        matcher.set_target(receive_key)

        if prompt is not None:
            result = await cls.send(prompt, **kwargs)
            matcher.state[REJECT_PROMPT_RESULT_KEY.format(key=receive_key)] = (
                result
            )
        raise RejectedException

    @classmethod
    def skip(cls) -> NoReturn:
        """Skip the current handler and continue with the next handler.

        This is commonly raised while resolving a handler dependency.
        """
        raise SkippedException

    @overload
    def get_receive(self, id: str) -> Event | None: ...

    @overload
    def get_receive(self, id: str, default: T) -> Event | T: ...

    def get_receive(
        self, id: str, default: T | None = None
    ) -> Event | T | None:
        """Return an event stored by receive.

        Return default if no event exists for the identifier.
        """
        return self.state.get(RECEIVE_KEY.format(id=id), default)

    def set_receive(self, id: str, event: Event) -> None:
        """Store an event for a receive identifier."""
        self.state[RECEIVE_KEY.format(id=id)] = event
        self.state[LAST_RECEIVE_KEY] = event

    @overload
    def get_last_receive(self) -> Event | None: ...

    @overload
    def get_last_receive(self, default: T) -> Event | T: ...

    def get_last_receive(self, default: T | None = None) -> Event | T | None:
        """Return the most recently received event.

        Return default if no event has been received.
        """
        return self.state.get(LAST_RECEIVE_KEY, default)

    @overload
    def get_arg(self, key: str) -> Message | None: ...

    @overload
    def get_arg(self, key: str, default: T) -> Message | T: ...

    def get_arg(self, key: str, default: T | None = None) -> Message | T | None:
        """Return a message collected by got.

        Return default if no message exists for the argument.
        """
        return self.state.get(ARG_KEY.format(key=key), default)

    def set_arg(self, key: str, message: Message) -> None:
        """Store a message collected by got."""
        self.state[ARG_KEY.format(key=key)] = message

    def set_target(self, target: str, cache: bool = True) -> None:
        """Store the next rejection target, optionally deferring activation."""
        if cache:
            self.state[REJECT_CACHE_TARGET] = target
        else:
            self.state[REJECT_TARGET] = target

    @overload
    def get_target(self) -> str | None: ...

    @overload
    def get_target(self, default: T) -> str | T: ...

    def get_target(self, default: T | None = None) -> str | T | None:
        """Return the active rejection target or the supplied default."""
        return self.state.get(REJECT_TARGET, default)

    def stop_propagation(self):
        """Stop propagation to lower-priority matchers."""
        self.block = True

    async def update_type(
        self,
        channel: Channel,
        event: Event,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ) -> str:
        """Resolve the event type for a resumed conversation matcher."""
        updater = self.__class__._default_type_updater
        return (
            await updater(
                channel=channel,
                event=event,
                state=self.state,
                matcher=self,
                stack=stack,
                dependency_cache=dependency_cache,
                invocation_context=invocation_context,
            )
            if updater
            else "message"
        )

    async def update_permission(
        self,
        channel: Channel,
        event: Event,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ) -> Permission:
        """Resolve continuation access, defaulting to the current user."""
        if updater := self.__class__._default_permission_updater:
            return await updater(
                channel=channel,
                event=event,
                state=self.state,
                matcher=self,
                stack=stack,
                dependency_cache=dependency_cache,
                invocation_context=invocation_context,
            )
        return Permission(User.from_event(event, perm=self.permission))

    async def resolve_reject(self):
        """Requeue the current handler and activate its cached target."""
        handler = current_handler.get()
        self.remain_handlers.insert(0, handler)
        if REJECT_CACHE_TARGET in self.state:
            self.state[REJECT_TARGET] = self.state[REJECT_CACHE_TARGET]

    @contextmanager
    def ensure_context(self, channel: Channel, event: Event):
        """Bind channel, event, and matcher context until the scope exits."""
        b_t = current_channel.set(channel)
        e_t = current_event.set(event)
        m_t = current_matcher.set(self)
        try:
            yield
        finally:
            current_channel.reset(b_t)
            current_event.reset(e_t)
            current_matcher.reset(m_t)

    async def simple_run(
        self,
        channel: Channel,
        event: Event,
        state: T_State,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ):
        """Execute remaining handlers with the incoming state and context."""
        logger.debug("Running %s with incoming context", self)

        with self.ensure_context(channel, event):
            try:
                # Refresh preprocess state
                self.state.update(state)

                while self.remain_handlers:
                    handler = self.remain_handlers.pop(0)
                    current_handler.set(handler)
                    logger.debug(f"Running handler {handler}")

                    try:
                        await handler(
                            matcher=self,
                            channel=channel,
                            event=event,
                            state=self.state,
                            stack=stack,
                            dependency_cache=dependency_cache,
                            invocation_context=invocation_context,
                        )
                    except* SkippedException:
                        logger.debug(f"Handler {handler} skipped")
            except* StopPropagation:
                self.block = True
            finally:
                logger.info(f"{self} running complete")

    # Run the remaining handlers.
    async def run(
        self,
        channel: Channel,
        event: Event,
        state: T_State,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        invocation_context: Any = None,
    ):
        """Run handlers and resume paused or rejected conversations."""
        exc: FinishedException | RejectedException | PausedException | None = (
            None
        )

        try:
            await self.simple_run(
                channel,
                event,
                state,
                stack,
                dependency_cache,
                invocation_context,
            )
        except* (
            FinishedException,
            RejectedException,
            PausedException,
        ) as exc_group:
            excs = list(flatten_exception_group(exc_group))
            if len(excs) > 1:
                logger.warning(
                    "Multiple session controls occurred; applying precedence."
                )
                finished_exc = next(
                    (e for e in excs if isinstance(e, FinishedException)),
                    None,
                )
                rejected_exc = next(
                    (e for e in excs if isinstance(e, RejectedException)),
                    None,
                )
                paused_exc = next(
                    (e for e in excs if isinstance(e, PausedException)),
                    None,
                )
                exc = finished_exc or rejected_exc or paused_exc
            elif isinstance(
                excs[0], (FinishedException, RejectedException, PausedException)
            ):
                exc = excs[0]

        if isinstance(exc, FinishedException):
            pass
        elif isinstance(exc, RejectedException):
            self.suspended = True
            await self.resolve_reject()
            type_ = await self.update_type(
                channel, event, stack, dependency_cache, invocation_context
            )
            permission = await self.update_permission(
                channel, event, stack, dependency_cache, invocation_context
            )

            self.new(
                type_,
                Rule(),
                permission,
                self.remain_handlers,
                temp=True,
                priority=0,
                block=True,
                source=self.__class__._source,
                expire_time=channel.config.session_expire_timeout,
                default_state=self.state,
                default_type_updater=self.__class__._default_type_updater,
                default_permission_updater=self.__class__._default_permission_updater,
            )
        elif isinstance(exc, PausedException):
            self.suspended = True
            type_ = await self.update_type(
                channel, event, stack, dependency_cache, invocation_context
            )
            permission = await self.update_permission(
                channel, event, stack, dependency_cache, invocation_context
            )

            self.new(
                type_,
                Rule(),
                permission,
                self.remain_handlers,
                temp=True,
                priority=0,
                block=True,
                source=self.__class__._source,
                expire_time=channel.config.session_expire_timeout,
                default_state=self.state,
                default_type_updater=self.__class__._default_type_updater,
                default_permission_updater=self.__class__._default_permission_updater,
            )
