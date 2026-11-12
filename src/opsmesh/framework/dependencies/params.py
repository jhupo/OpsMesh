"""Dependency descriptors, resolution, and shared result caching."""

import inspect
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from enum import StrEnum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    Self,
    cast,
    get_args,
    get_origin,
)

import anyio
from pydantic.fields import FieldInfo as PydanticFieldInfo
from typing_extensions import override

from opsmesh.framework.compat import FieldInfo, ModelField, PydanticUndefined
from opsmesh.framework.consts import ARG_KEY, REJECT_PROMPT_RESULT_KEY
from opsmesh.framework.dependencies import Dependent, Param
from opsmesh.framework.dependencies.utils import check_field_type
from opsmesh.framework.exceptions import SkippedException
from opsmesh.framework.typing import (
    _STATE_FLAG,
    T_DependencyCache,
    T_Handler,
    T_State,
    origin_is_annotated,
)
from opsmesh.framework.utils import (
    generic_check_issubclass,
    get_name,
    is_async_gen_callable,
    is_coroutine_callable,
    is_gen_callable,
    run_sync,
    run_sync_ctx_manager,
)

if TYPE_CHECKING:
    from opsmesh.framework.adapters import Channel, Event, Message
    from opsmesh.framework.matchers import Matcher


class DependsInner:
    """Describe a dependency callable, caching, and validation policy."""

    def __init__(
        self,
        dependency: T_Handler | None = None,
        *,
        use_cache: bool = True,
        validate: bool | PydanticFieldInfo = False,
    ) -> None:
        self.dependency = dependency
        self.use_cache = use_cache
        self.validate = validate

    def __repr__(self) -> str:
        dep = get_name(self.dependency)
        cache = "" if self.use_cache else ", use_cache=False"
        validate = f", validate={self.validate}" if self.validate else ""
        return f"DependsInner({dep}{cache}{validate})"


def Depends(
    dependency: T_Handler | None = None,
    *,
    use_cache: bool = True,
    validate: bool | PydanticFieldInfo = False,
) -> Any:
    """A descriptor for a nested dependency.

    Args:
        dependency: The dependency callable; defaults to the parameter
            annotation.
        use_cache: Whether to cache the result; defaults to True.
        validate: Whether to validate through Pydantic; defaults to False.

    Examples:
        ```python
        def depend_func() -> Any:
            return ...


        def depend_gen_func():
            try:
                yield ...
            finally:
                ...


        async def handler(
            param_name: Any = Depends(depend_func),
            gen: Any = Depends(depend_gen_func),
        ): ...
        ```
    """
    return DependsInner(dependency, use_cache=use_cache, validate=validate)


class CacheState(StrEnum):
    """Resolution state of a cached dependency."""

    PENDING = "PENDING"
    FINISHED = "FINISHED"


class DependencyCache:
    """A cache entry for a nested dependency result.

    Shares one resolution result across consumers to avoid duplicate
    execution.
    """

    def __init__(self):
        self._state = CacheState.PENDING
        self._result: Any = None
        self._exception: BaseException | None = None
        self._waiter = anyio.Event()

    def done(self) -> bool:
        """Return whether dependency resolution has finished."""
        return self._state == CacheState.FINISHED

    def result(self) -> Any:
        """Return the cached dependency result."""
        if self._state != CacheState.FINISHED:
            raise RuntimeError("Result is not ready")

        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self) -> BaseException | None:
        """Return the cached dependency exception."""
        if self._state != CacheState.FINISHED:
            raise RuntimeError("Result is not ready")

        return self._exception

    def set_result(self, result: Any) -> None:
        """Store the dependency result."""
        if self._state != CacheState.PENDING:
            raise RuntimeError(f"Cache state invalid: {self._state}")

        self._result = result
        self._state = CacheState.FINISHED
        self._waiter.set()

    def set_exception(self, exception: BaseException) -> None:
        """Store the dependency exception."""
        if self._state != CacheState.PENDING:
            raise RuntimeError(f"Cache state invalid: {self._state}")

        self._exception = exception
        self._state = CacheState.FINISHED
        self._waiter.set()

    async def wait(self):
        """Wait until dependency resolution finishes."""
        await self._waiter.wait()
        if self._state != CacheState.FINISHED:
            raise RuntimeError("Invalid cache state")

        if self._exception is not None:
            raise self._exception

        return self._result


class DependParam(Param):
    """A parameter resolved by evaluating nested dependencies.

    Resolve child dependencies and pass their return values to the parent
    call.

    This descriptor must be checked before other parameter descriptor types.
    """

    def __init__(
        self, *args, dependent: Dependent[Any], use_cache: bool, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.dependent = dependent
        self.use_cache = use_cache

    def __repr__(self) -> str:
        return f"Depends({self.dependent}, use_cache={self.use_cache})"

    @classmethod
    def _from_field(
        cls,
        sub_dependent: Dependent[Any],
        use_cache: bool,
        validate: bool | PydanticFieldInfo,
    ) -> Self:
        return cls._inherit_construct(
            validate if isinstance(validate, PydanticFieldInfo) else None,
            dependent=sub_dependent,
            use_cache=use_cache,
            validate=bool(validate),
        )

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        type_annotation, depends_inner = param.annotation, None
        # extract type annotation and dependency from Annotated
        if get_origin(param.annotation) is Annotated:
            type_annotation, *extra_args = get_args(param.annotation)
            depends_inner = next(
                (
                    x
                    for x in reversed(extra_args)
                    if isinstance(x, DependsInner)
                ),
                None,
            )

        # param default value takes higher priority
        depends_inner = (
            param.default
            if isinstance(param.default, DependsInner)
            else depends_inner
        )
        # not a dependent
        if depends_inner is None:
            return

        dependency: T_Handler
        # sub dependency is not specified, use type annotation
        if depends_inner.dependency is None:
            assert type_annotation is not inspect.Signature.empty, (
                "Dependency cannot be empty"
            )
            dependency = type_annotation
        else:
            dependency = depends_inner.dependency
        # parse sub dependency
        sub_dependent = Dependent[Any].parse(
            call=dependency,
            allow_types=allow_types,
        )

        return cls._from_field(
            sub_dependent, depends_inner.use_cache, depends_inner.validate
        )

    @classmethod
    @override
    def _check_parameterless(
        cls, value: Any, allow_types: tuple[type[Param], ...]
    ) -> "Param | None":
        if isinstance(value, DependsInner):
            assert value.dependency, "Dependency cannot be empty"
            dependent = Dependent[Any].parse(
                call=value.dependency, allow_types=allow_types
            )
            return cls._from_field(dependent, value.use_cache, value.validate)

    @override
    async def _solve(
        self,
        stack: AsyncExitStack | None = None,
        dependency_cache: T_DependencyCache | None = None,
        **kwargs: Any,
    ) -> Any:
        use_cache: bool = self.use_cache
        dependency_cache = {} if dependency_cache is None else dependency_cache

        sub_dependent = self.dependent
        call = cast(Callable[..., Any], sub_dependent.call)

        # solve sub dependency with current cache
        exc: BaseExceptionGroup[SkippedException] | None = None

        try:
            sub_values = await sub_dependent.solve(
                stack=stack,
                dependency_cache=dependency_cache,
                **kwargs,
            )
        except* SkippedException as exc_group:
            exc = exc_group

        else:
            # run dependency function
            if use_cache and call in dependency_cache:
                return await dependency_cache[call].wait()

            if is_gen_callable(call) or is_async_gen_callable(call):
                assert isinstance(stack, AsyncExitStack), (
                    "Generator dependency should be called in context"
                )
                if is_gen_callable(call):
                    cm = run_sync_ctx_manager(
                        contextmanager(call)(**sub_values)
                    )
                else:
                    cm = asynccontextmanager(call)(**sub_values)

                target = stack.enter_async_context(cm)
            elif is_coroutine_callable(call):
                target = call(**sub_values)
            else:
                target = run_sync(call)(**sub_values)

            dependency_cache[call] = cache = DependencyCache()
            try:
                result = await target
            except Exception as e:
                cache.set_exception(e)
                raise
            except BaseException as e:
                cache.set_exception(e)
                # remove cache when base exception occurs
                # e.g. CancelledError
                dependency_cache.pop(call, None)
                raise
            else:
                cache.set_result(result)
                return result

        if exc is not None:
            raise exc

    @override
    async def _check(self, **kwargs: Any) -> None:
        # run sub dependent pre-checkers
        await self.dependent.check(**kwargs)


class ChannelParam(Param):
    """A parameter receiving the current Channel instance.

    Accepts Channel, its subclasses, or optional channel annotations.

    For compatibility, an unannotated parameter named channel is also
    accepted.
    """

    def __init__(
        self, *args, checker: ModelField | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.checker = checker

    def __repr__(self) -> str:
        return (
            "ChannelParam("
            + (
                repr(self.checker.annotation)
                if self.checker is not None
                else ""
            )
            + ")"
        )

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        from opsmesh.framework.adapters import Channel

        # param type is Channel(s) or subclass(es) of Channel or None
        if generic_check_issubclass(param.annotation, Channel):
            checker: ModelField | None = None
            if param.annotation is not Channel:
                checker = ModelField.construct(
                    name=param.name,
                    annotation=param.annotation,
                    field_info=FieldInfo(),
                )
            return cls(checker=checker)
        # legacy: param is named "channel" and has no type annotation
        elif param.annotation == param.empty and param.name == "channel":
            return cls()

    @override
    async def _solve(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, channel: "Channel", **kwargs: Any
    ) -> Any:
        return channel

    @override
    async def _check(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, channel: "Channel", **kwargs: Any
    ) -> None:
        if self.checker is not None:
            check_field_type(self.checker, channel)


class EventParam(Param):
    """A parameter receiving the current Event instance.

    Accepts Event, its subclasses, or optional event annotations.

    For compatibility, an unannotated parameter named event is also accepted.
    """

    def __init__(
        self, *args, checker: ModelField | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.checker = checker

    def __repr__(self) -> str:
        return (
            "EventParam("
            + (
                repr(self.checker.annotation)
                if self.checker is not None
                else ""
            )
            + ")"
        )

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        from opsmesh.framework.adapters import Event

        # param type is Event(s) or subclass(es) of Event or None
        if generic_check_issubclass(param.annotation, Event):
            checker: ModelField | None = None
            if param.annotation is not Event:
                checker = ModelField.construct(
                    name=param.name,
                    annotation=param.annotation,
                    field_info=FieldInfo(),
                )
            return cls(checker=checker)
        # legacy: param is named "event" and has no type annotation
        elif param.annotation == param.empty and param.name == "event":
            return cls()

    @override
    async def _solve(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, event: "Event", **kwargs: Any
    ) -> Any:
        return event

    @override
    async def _check(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, event: "Event", **kwargs: Any
    ) -> Any:
        if self.checker is not None:
            check_field_type(self.checker, event)


class StateParam(Param):
    """A parameter receiving the current event-processing state.

    Accepts parameters annotated as T_State.

    For compatibility, an unannotated parameter named state is also accepted.
    """

    def __repr__(self) -> str:
        return "StateParam()"

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        # param type is T_State
        if origin_is_annotated(
            get_origin(param.annotation)
        ) and _STATE_FLAG in get_args(param.annotation):
            return cls()
        # legacy: param is named "state" and has no type annotation
        elif param.annotation == param.empty and param.name == "state":
            return cls()

    @override
    async def _solve(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, state: T_State, **kwargs: Any
    ) -> Any:
        return state


class MatcherParam(Param):
    """A parameter receiving the current matcher instance.

    Accepts Matcher, its subclasses, or optional matcher annotations.

    For compatibility, an unannotated parameter named matcher is also
    accepted.
    """

    def __init__(
        self, *args, checker: ModelField | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.checker = checker

    def __repr__(self) -> str:
        return (
            "MatcherParam("
            + (
                repr(self.checker.annotation)
                if self.checker is not None
                else ""
            )
            + ")"
        )

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        from opsmesh.framework.matchers import Matcher

        # param type is Matcher(s) or subclass(es) of Matcher or None
        if generic_check_issubclass(param.annotation, Matcher):
            checker: ModelField | None = None
            if param.annotation is not Matcher:
                checker = ModelField.construct(
                    name=param.name,
                    annotation=param.annotation,
                    field_info=FieldInfo(),
                )
            return cls(checker=checker)
        # legacy: param is named "matcher" and has no type annotation
        elif param.annotation == param.empty and param.name == "matcher":
            return cls()

    @override
    async def _solve(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, matcher: "Matcher", **kwargs: Any
    ) -> Any:
        return matcher

    @override
    async def _check(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, matcher: "Matcher", **kwargs: Any
    ) -> Any:
        if self.checker is not None:
            check_field_type(self.checker, matcher)


class ArgInner:
    """Describe a collected argument and its message conversion mode."""

    def __init__(
        self,
        key: str | None,
        type: Literal["message", "str", "plaintext", "prompt"],
    ) -> None:
        self.key: str | None = key
        self.type: Literal["message", "str", "plaintext", "prompt"] = type

    def __repr__(self) -> str:
        return f"ArgInner(key={self.key!r}, type={self.type!r})"


def Arg(key: str | None = None) -> Any:
    """Return a descriptor for a collected argument message."""
    return ArgInner(key, "message")


def ArgStr(key: str | None = None) -> str:
    """Return a descriptor for a collected argument's string representation."""
    return ArgInner(key, "str")  # type: ignore


def ArgPlainText(key: str | None = None) -> str:
    """Return a descriptor for a collected argument's plain text."""
    return ArgInner(key, "plaintext")  # type: ignore


def ArgPromptResult(key: str | None = None) -> Any:
    """Return a descriptor for the argument prompt's send result."""
    return ArgInner(key, "prompt")


class ArgParam(Param):
    """A parameter receiving an argument collected by a matcher.

    Resolves arguments collected through the matcher's got decorator.

    Select the argument using key in Arg, ArgStr, or ArgPlainText;
    when omitted, the handler parameter name is used.
    """

    def __init__(
        self,
        *args,
        key: str,
        type: Literal["message", "str", "plaintext", "prompt"],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.key = key
        self.type = type

    def __repr__(self) -> str:
        return f"ArgParam(key={self.key!r}, type={self.type!r})"

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        if isinstance(param.default, ArgInner):
            return cls(
                key=param.default.key or param.name, type=param.default.type
            )
        elif get_origin(param.annotation) is Annotated:
            for arg in get_args(param.annotation)[:0:-1]:
                if isinstance(arg, ArgInner):
                    return cls(key=arg.key or param.name, type=arg.type)

    async def _solve(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, matcher: "Matcher", **kwargs: Any
    ) -> Any:
        if self.type == "message":
            return self._solve_message(matcher)
        elif self.type == "str":
            return self._solve_str(matcher)
        elif self.type == "plaintext":
            return self._solve_plaintext(matcher)
        elif self.type == "prompt":
            return self._solve_prompt(matcher)
        else:
            raise ValueError(f"Unknown Arg type: {self.type}")

    def _solve_message(self, matcher: "Matcher") -> "Message | None":
        return matcher.get_arg(self.key)

    def _solve_str(self, matcher: "Matcher") -> str | None:
        message = matcher.get_arg(self.key)
        return str(message) if message is not None else None

    def _solve_plaintext(self, matcher: "Matcher") -> str | None:
        message = matcher.get_arg(self.key)
        return message.extract_plain_text() if message is not None else None

    def _solve_prompt(self, matcher: "Matcher") -> Any | None:
        return matcher.state.get(
            REJECT_PROMPT_RESULT_KEY.format(key=ARG_KEY.format(key=self.key))
        )


class ExceptionParam(Param):
    """A parameter receiving an exception in a matcher postprocessor.

    Accepts Exception or optional exception annotations.

    For compatibility, an unannotated parameter named exception is accepted.
    """

    def __repr__(self) -> str:
        return "ExceptionParam()"

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        # param type is Exception(s) or subclass(es) of Exception or None
        if generic_check_issubclass(param.annotation, Exception):
            return cls()
        # legacy: param is named "exception" and has no type annotation
        elif param.annotation == param.empty and param.name == "exception":
            return cls()

    @override
    async def _solve(
        self, exception: Exception | None = None, **kwargs: Any
    ) -> Any:
        return exception


class DefaultParam(Param):
    """A parameter resolved to its declared default value.

    Handles remaining parameters that have a default but no other resolver.

    Check this descriptor last so more specific resolvers take precedence.
    """

    def __repr__(self) -> str:
        return f"DefaultParam(default={self.default!r})"

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Self | None:
        if param.default != param.empty:
            return cls(default=param.default)

    @override
    async def _solve(self, **kwargs: Any) -> Any:
        return PydanticUndefined


__autodoc__ = {
    "DependsInner": False,
    "StateInner": False,
    "ArgInner": False,
}
