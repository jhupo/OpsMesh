"""Shared helpers for typing, execution, imports, and serialization."""

import asyncio
import contextlib
import dataclasses
import importlib
import inspect
import json
import logging
from collections import deque
from collections.abc import (
    AsyncGenerator,
    Callable,
    Coroutine,
    Generator,
    Mapping,
    Sequence,
)
from contextlib import AbstractContextManager, asynccontextmanager
from enum import Enum
from functools import partial, wraps
from pathlib import Path
from typing import (
    Any,
    Final,
    Generic,
    Literal,
    ParamSpec,
    TypeAlias,
    TypeVar,
    final,
    get_args,
    get_origin,
    overload,
)

import anyio
import anyio.to_thread
from pydantic import BaseModel
from typing_extensions import override

from opsmesh.typing import (
    all_literal_values,
    is_none_type,
    origin_is_literal,
    origin_is_union,
    type_has_args,
)

from .compat import custom_validation

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")
E = TypeVar("E", bound=BaseException)


@final
@custom_validation
class Unset(Enum):
    """False-valued singleton marker for an omitted value."""

    _UNSET = "<UNSET>"

    def __repr__(self) -> str:
        return "<UNSET>"

    def __str__(self) -> str:
        return self.__repr__()

    def __bool__(self) -> Literal[False]:
        return False

    def __copy__(self):
        return self._UNSET

    def __deepcopy__(self, memo: dict[int, Any]):
        return self._UNSET

    @classmethod
    def __get_validators__(cls):
        yield cls._validate

    @classmethod
    def _validate(cls, value: Any):
        if value is not cls._UNSET:
            raise ValueError(f"{value!r} is not UNSET")
        return value


UnsetType: TypeAlias = Literal[Unset._UNSET]

UNSET: Final[UnsetType] = Unset._UNSET


def exclude_unset(data: Any) -> Any:
    """Recursively remove omitted values from dictionaries and lists."""
    if isinstance(data, dict):
        return data.__class__(
            (k, exclude_unset(v)) for k, v in data.items() if v is not UNSET
        )
    elif isinstance(data, list):
        return data.__class__(exclude_unset(i) for i in data if i is not UNSET)
    elif data is UNSET:
        return None
    return data


def deep_update(
    mapping: dict[K, Any], *updating_mappings: dict[K, Any]
) -> dict[K, Any]:
    """Recursively merge dictionaries into a new mapping."""
    updated_mapping = mapping.copy()
    for updating_mapping in updating_mappings:
        for k, v in updating_mapping.items():
            if (
                k in updated_mapping
                and isinstance(updated_mapping[k], dict)
                and isinstance(v, dict)
            ):
                updated_mapping[k] = deep_update(updated_mapping[k], v)
            else:
                updated_mapping[k] = v
    return updated_mapping


def lenient_issubclass(
    cls: Any, class_or_tuple: type[Any] | tuple[type[Any], ...]
) -> bool:
    """Check subclass membership, treating invalid class arguments as False."""
    try:
        return isinstance(cls, type) and issubclass(cls, class_or_tuple)
    except TypeError:
        return False


def generic_check_issubclass(
    cls: Any, class_or_tuple: type[Any] | tuple[type[Any], ...]
) -> bool:
    """Check subclass membership while accounting for typing annotations.

    Special cases:

    - For TypeVar annotations,
      inspect the bound or constraints
      and accept subclasses or None.
    - For typing.Union and types.UnionType,
      every member must be an accepted subclass or None.
    - For Literal annotations,
      every value must be an instance of an accepted class.
    - For parameterized generics such as list or dict,
      inspect the generic's origin class.
    """
    # if the target is a TypeVar, we check it first
    if isinstance(cls, TypeVar):
        if cls.__constraints__:
            return all(
                is_none_type(type_)
                or generic_check_issubclass(type_, class_or_tuple)
                for type_ in cls.__constraints__
            )
        elif cls.__bound__:
            return generic_check_issubclass(cls.__bound__, class_or_tuple)
        return False
    # elif the target is not a generic type, we check it directly
    elif not type_has_args(cls):
        with contextlib.suppress(TypeError):
            return issubclass(cls, class_or_tuple)

    origin = get_origin(cls)
    if origin_is_union(origin):
        return all(
            is_none_type(type_)
            or generic_check_issubclass(type_, class_or_tuple)
            for type_ in get_args(cls)
        )
    elif origin_is_literal(origin):
        return all(
            is_none_type(value) or isinstance(value, class_or_tuple)
            for value in all_literal_values(cls)
        )
    # ensure generic List, Dict can be checked
    elif origin:
        # avoid class check error (typing.Final, typing.ClassVar, etc...)
        try:
            return issubclass(origin, class_or_tuple)
        except TypeError:
            return False
    return False


def type_is_complex(type_: type[Any]) -> bool:
    """Check whether an annotation requires complex-value parsing."""
    origin = get_origin(type_)
    return _type_is_complex_inner(type_) or _type_is_complex_inner(origin)


def _type_is_complex_inner(type_: type[Any] | None) -> bool:
    if lenient_issubclass(type_, (str, bytes)):
        return False

    return lenient_issubclass(
        type_, (BaseModel, Mapping, Sequence, tuple, set, frozenset, deque)
    ) or dataclasses.is_dataclass(type_)


def is_coroutine_callable(call: Callable[..., Any]) -> bool:
    """Check whether a callable is an asynchronous coroutine function."""
    if inspect.isroutine(call):
        return inspect.iscoroutinefunction(call)
    if inspect.isclass(call):
        return False
    func_ = getattr(call, "__call__", None)  # noqa: B004
    return inspect.iscoroutinefunction(func_)


def is_gen_callable(call: Callable[..., Any]) -> bool:
    """Check whether a callable is a generator function."""
    if inspect.isgeneratorfunction(call):
        return True
    func_ = getattr(call, "__call__", None)  # noqa: B004
    return inspect.isgeneratorfunction(func_)


def is_async_gen_callable(call: Callable[..., Any]) -> bool:
    """Check whether a callable is an asynchronous generator function."""
    if inspect.isasyncgenfunction(call):
        return True
    func_ = getattr(call, "__call__", None)  # noqa: B004
    return inspect.isasyncgenfunction(func_)


def run_sync(call: Callable[P, R]) -> Callable[P, Coroutine[None, None, R]]:
    """Wrap a synchronous callable for asynchronous execution.

    Args:
        call: The synchronous callable to wrap.
    """

    @wraps(call)
    async def _wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        worker = asyncio.create_task(
            anyio.to_thread.run_sync(partial(call, *args, **kwargs))
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Dependencies must outlive a thread that cannot be cancelled.
            with anyio.CancelScope(shield=True):
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
            if not worker.cancelled():
                worker.exception()
            raise

    return _wrapper


@asynccontextmanager
async def run_sync_ctx_manager(
    cm: AbstractContextManager[T],
) -> AsyncGenerator[T, None]:
    """Adapt a synchronous context manager with cancellation-safe cleanup."""
    entered = False

    def enter() -> T:
        nonlocal entered
        value = cm.__enter__()
        entered = True
        return value

    try:
        with anyio.CancelScope(shield=True):
            value = await run_sync(enter)()
    except BaseException as error:
        if entered:
            # Cancellation can arrive after acquisition but before yield.
            with anyio.CancelScope(shield=True):
                await run_sync(cm.__exit__)(
                    type(error), error, error.__traceback__
                )
        raise
    try:
        yield value
    except BaseException as error:
        with anyio.CancelScope(shield=True):
            suppressed = await run_sync(cm.__exit__)(
                type(error), error, error.__traceback__
            )
        if not suppressed:
            raise
    else:
        with anyio.CancelScope(shield=True):
            await run_sync(cm.__exit__)(None, None, None)


@overload
async def run_coro_with_catch(
    coro: Coroutine[Any, Any, T],
    exc: tuple[type[Exception], ...],
    return_on_err: None = None,
) -> T | None: ...


@overload
async def run_coro_with_catch(
    coro: Coroutine[Any, Any, T],
    exc: tuple[type[Exception], ...],
    return_on_err: R,
) -> T | R: ...


async def run_coro_with_catch(
    coro: Coroutine[Any, Any, T],
    exc: tuple[type[Exception], ...],
    return_on_err: R | None = None,
) -> T | R | None:
    """Run a coroutine and return a fallback for selected exceptions.

    Args:
        coro: The coroutine to run.
        exc: The exception type or types to catch.
        return_on_err: The fallback returned when an exception is caught.

    Returns:
        The coroutine result, or the fallback after a caught exception.
    """
    try:
        return await coro
    except* exc:
        pass

    return return_on_err


async def run_coro_with_shield(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine while shielding it from cancellation.

    Args:
        coro: The coroutine to run.

    Returns:
        The coroutine's return value.
    """
    with anyio.CancelScope(shield=True):
        return await coro

    raise RuntimeError("This should not happen")


def flatten_exception_group(
    exc_group: BaseExceptionGroup[E],
) -> Generator[E, None, None]:
    """Yield leaf exceptions from nested exception groups."""
    for exc in exc_group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from flatten_exception_group(exc)
        else:
            yield exc


def get_name(obj: Any) -> str:
    """Return a callable or object's display name."""
    if inspect.isfunction(obj) or inspect.isclass(obj):
        return obj.__name__
    return obj.__class__.__name__


def path_to_module_name(path: Path) -> str:
    """Convert a source path into a dotted module name."""
    rel_path = path.resolve().relative_to(Path.cwd().resolve())
    if rel_path.stem == "__init__":
        return ".".join(rel_path.parts[:-1])
    else:
        return ".".join((*rel_path.parts[:-1], rel_path.stem))


def resolve_dot_notation(
    obj_str: str, default_attr: str, default_prefix: str | None = None
) -> Any:
    """Resolve and import an object using dotted module notation."""
    modulename, _, cls = obj_str.partition(":")
    if default_prefix is not None and modulename.startswith("~"):
        modulename = default_prefix + modulename[1:]
    module = importlib.import_module(modulename)
    if not cls:
        return getattr(module, default_attr)
    instance = module
    for attr_str in cls.split("."):
        instance = getattr(instance, attr_str)
    return instance


class classproperty(Generic[T]):
    """A descriptor exposing a class-level property."""

    def __init__(self, func: Callable[[Any], T]) -> None:
        self.func = func

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> T:
        return self.func(type(instance) if owner is None else owner)


class DataclassEncoder(json.JSONEncoder):
    """A JSON encoder supporting message segments and other dataclasses."""

    @override
    def default(self, o):
        if dataclasses.is_dataclass(o):
            return {f.name: getattr(o, f.name) for f in dataclasses.fields(o)}
        return super().default(o)


def logger_wrapper(logger_name: str):
    """Create a logger bound to an adapter name.

    Args:
        logger_name: The adapter name used in logs.

    Returns:
        The adapter logging callable.

        Logging callable arguments:

        level: The log level.
        message: The log message.
        exception: Optional exception information.
    """

    def log(level: str, message: str, exception: Exception | None = None):
        levels = {
            **logging.getLevelNamesMapping(),
            "TRACE": logging.DEBUG,
            "SUCCESS": logging.INFO,
        }
        logger.log(
            levels[level.upper()],
            f"{logger_name} | {message}",
            exc_info=(type(exception), exception, exception.__traceback__)
            if exception is not None
            else None,
        )

    return log
