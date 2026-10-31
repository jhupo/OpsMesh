"""Callable dependency injection with explicit scope and resource ownership."""

from __future__ import annotations

import asyncio
import inspect
import types
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from opsmesh.framework.exception import DependencyError, MatcherSkipped

from .utils import typed_signature, unwrap


@dataclass(frozen=True)
class Dependency:
    dependency: Callable[..., Any] | None = None
    use_cache: bool = True


def Depends(dependency: Callable[..., Any] | None = None, *, use_cache: bool = True) -> Any:
    """Use a callable directly; Annotated[T, Depends(...)] is also supported."""
    return Dependency(dependency, use_cache)


@dataclass(frozen=True)
class DependencyParameter:
    name: str
    annotation: Any
    default: Any
    dependency: CallableDependency | None = None
    use_cache: bool = True


@dataclass(frozen=True)
class CallableDependency:
    call: Callable[..., Any]
    parameters: tuple[DependencyParameter, ...]

    @classmethod
    def parse(
        cls, call: Callable[..., Any], *, _path: tuple[Callable[..., Any], ...] = ()
    ) -> CallableDependency:
        if call in _path:
            names = " -> ".join(getattr(c, "__name__", repr(c)) for c in (*_path, call))
            raise DependencyError(f"Dependency cycle: {names}")
        parameters = []
        for p in typed_signature(call).parameters.values():
            annotation, metadata = unwrap(p.annotation)
            declarations = [m for m in metadata if isinstance(m, Dependency)]
            if isinstance(p.default, Dependency):
                declarations.append(p.default)
            if len(declarations) > 1:
                raise DependencyError(f"Multiple Depends declarations for {p.name}")
            if declarations:
                declaration = declarations[0]
                provider = declaration.dependency or annotation
                if provider is inspect.Parameter.empty or not callable(provider):
                    raise DependencyError(f"Missing dependency callable for {p.name}")
                nested = cls.parse(provider, _path=(*_path, call))
                parameters.append(
                    DependencyParameter(
                        p.name, annotation, p.default, nested, declaration.use_cache
                    )
                )
            else:
                parameters.append(DependencyParameter(p.name, annotation, p.default))
        return cls(call, tuple(parameters))

    async def __call__(self, scope: DependencyScope) -> Any:
        return await scope.invoke(self)


class DependencyScope:
    """An invocation scope; forks share event resources/cache but override local values.

    A scope must be entered with async with. Cached dependencies may be shared by
    concurrent matchers, so mutable exclusive resources should use use_cache=False.
    """

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})
        self._stack = AsyncExitStack()
        self._cache: dict[Callable[..., Any], asyncio.Task[Any]] = {}
        self._root = self
        self._active = False

    async def __aenter__(self) -> DependencyScope:
        if self._active:
            raise RuntimeError("Dependency scope already entered")
        await self._stack.__aenter__()
        self._active = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            tasks = tuple(self._cache.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._stack.__aexit__(*exc)
        finally:
            self._active = False
            self._cache.clear()

    def fork(self, **values: Any) -> DependencyScope:
        child = DependencyScope({**self.values, **values})
        child._stack, child._cache, child._root = self._stack, self._cache, self._root
        return child

    def _value(self, parameter: DependencyParameter) -> Any:
        annotation = parameter.annotation
        origin = get_origin(annotation)
        args = get_args(annotation)
        optional = origin in (Union, types.UnionType) and type(None) in args
        candidates = tuple(t for t in args if t is not type(None)) if optional else (annotation,)
        if parameter.name in self.values:
            value = self.values[parameter.name]
            if value is None and not optional and parameter.default is inspect.Parameter.empty:
                raise MatcherSkipped(f"{parameter.name} is unavailable in this scope")
            if annotation not in (Any, inspect.Parameter.empty) and value is not None:
                concrete = tuple(
                    get_origin(t) or t
                    for t in candidates
                    if isinstance(t, type) or isinstance(get_origin(t), type)
                )
                if concrete and not isinstance(value, concrete):
                    raise MatcherSkipped(f"{parameter.name} does not match {annotation}")
            return value
        for value in self.values.values():
            for candidate in candidates:
                check = get_origin(candidate) or candidate
                if (
                    check not in (Any, inspect.Parameter.empty)
                    and isinstance(check, type)
                    and isinstance(value, check)
                ):
                    return value
        if parameter.default is not inspect.Parameter.empty:
            return parameter.default
        if optional:
            return None
        # Missing context-specific typed values are a non-applicable handler.
        if isinstance(annotation, type) and annotation.__module__.startswith("opsmesh.framework"):
            raise MatcherSkipped(f"{annotation.__name__} is unavailable in this scope")
        raise DependencyError(f"No value or Depends provider for parameter {parameter.name!r}")

    async def invoke(self, call: Callable[..., Any] | CallableDependency) -> Any:
        if not self._root._active:
            raise RuntimeError("Dependency scope is not active")
        dependent = call if isinstance(call, CallableDependency) else CallableDependency.parse(call)
        kwargs: dict[str, Any] = {}
        # Resolve ordinary parameters first so missing bot context doesn't allocate resources.
        for p in dependent.parameters:
            if p.dependency is None:
                kwargs[p.name] = self._value(p)
        for p in dependent.parameters:
            if p.dependency is not None:
                if p.use_cache:
                    task = self._cache.get(p.dependency.call)
                    if task is None:
                        task = asyncio.create_task(self.invoke(p.dependency))
                        self._cache[p.dependency.call] = task
                    kwargs[p.name] = await asyncio.shield(task)
                else:
                    kwargs[p.name] = await self.invoke(p.dependency)
        fn = dependent.call
        if inspect.isasyncgenfunction(fn):
            return await self._stack.enter_async_context(asynccontextmanager(fn)(**kwargs))
        if inspect.isgeneratorfunction(fn):
            # Sync context resources are intended for short, non-blocking enter/exit work.
            return self._stack.enter_context(contextmanager(fn)(**kwargs))
        if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(type(fn).__call__):
            return await fn(**kwargs)
        result = await asyncio.to_thread(fn, **kwargs)
        return await result if inspect.isawaitable(result) else result


__all__ = ["CallableDependency", "Dependency", "DependencyScope", "Depends"]
