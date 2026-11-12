"""Dependency definitions, parameter resolution, and callable execution."""

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Generic, TypeVar, cast

import anyio

from opsmesh.framework.compat import ModelField, PydanticUndefined
from opsmesh.framework.exceptions import SkippedException
from opsmesh.framework.typing import _DependentCallable
from opsmesh.framework.utils import (
    flatten_exception_group,
    is_coroutine_callable,
    run_coro_with_shield,
    run_sync,
)

from .base import Param
from .utils import check_field_type, get_typed_signature

R = TypeVar("R")
T = TypeVar("T", bound="Dependent")


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dependent(Generic[R]):
    """A parsed callable and its dependency-injection parameters.

    Args:
        call: The callable to invoke after resolving dependencies.
        params: Named parameters to resolve.
        parameterless: Dependencies that are not assigned to parameters.
    """

    call: _DependentCallable[R]
    params: tuple[ModelField, ...] = field(default_factory=tuple)
    parameterless: tuple[Param, ...] = field(default_factory=tuple)

    def __repr__(self) -> str:
        if inspect.isfunction(self.call) or inspect.isclass(self.call):
            call_str = self.call.__name__
        else:
            call_str = repr(self.call)
        return (
            f"Dependent(call={call_str}"
            + (
                f", parameterless={self.parameterless}"
                if self.parameterless
                else ""
            )
            + ")"
        )

    async def __call__(self, **kwargs: Any) -> R:
        """Check dependencies, resolve arguments, and invoke the callable."""
        exception: BaseExceptionGroup[SkippedException] | None = None

        try:
            # do pre-check
            await self.check(**kwargs)

            # solve param values
            values = await self.solve(**kwargs)

            # call function
            if is_coroutine_callable(self.call):
                return await cast(Callable[..., Awaitable[R]], self.call)(
                    **values
                )
            else:
                return await run_sync(cast(Callable[..., R], self.call))(
                    **values
                )
        except* SkippedException as exc_group:
            exception = exc_group
            excs = list(flatten_exception_group(exc_group))
            logger.debug(f"{self} skipped due to {excs}")

        raise exception

    @staticmethod
    def parse_params(
        call: _DependentCallable[R], allow_types: tuple[type[Param], ...]
    ) -> tuple[ModelField, ...]:
        """Parse a callable signature into validated dependency fields."""
        fields: list[ModelField] = []
        params = get_typed_signature(call).parameters.values()

        for param in params:
            if isinstance(param.default, Param):
                field_info = param.default
            else:
                for allow_type in allow_types:
                    if field_info := allow_type._check_param(
                        param, allow_types
                    ):
                        break
                else:
                    raise ValueError(
                        f"Unknown parameter {param.name} "
                        f"for function {call} with type {param.annotation}"
                    )

            annotation: Any = Any
            if param.annotation is not param.empty:
                annotation = param.annotation

            fields.append(
                ModelField.construct(
                    name=param.name,
                    annotation=annotation,
                    field_info=field_info,
                )
            )

        return tuple(fields)

    @staticmethod
    def parse_parameterless(
        parameterless: tuple[Any, ...], allow_types: tuple[type[Param], ...]
    ) -> tuple[Param, ...]:
        """Resolve descriptors for dependencies without named arguments."""
        parameterless_params: list[Param] = []
        for value in parameterless:
            for allow_type in allow_types:
                if param := allow_type._check_parameterless(value, allow_types):
                    break
            else:
                raise ValueError(f"Unknown parameterless {value}")
            parameterless_params.append(param)
        return tuple(parameterless_params)

    @classmethod
    def parse(
        cls,
        *,
        call: _DependentCallable[R],
        parameterless: Iterable[Any] | None = None,
        allow_types: Iterable[type[Param]],
    ) -> "Dependent[R]":
        """Build a dependent callable from its signature and descriptors."""
        allow_types = tuple(allow_types)

        params = cls.parse_params(call, allow_types)
        parameterless_params = (
            ()
            if parameterless is None
            else cls.parse_parameterless(tuple(parameterless), allow_types)
        )

        return cls(call, params, parameterless_params)

    async def check(self, **params: Any) -> None:
        """Run dependency preconditions concurrently before resolution."""
        if self.parameterless:
            async with anyio.create_task_group() as tg:
                for param in self.parameterless:
                    tg.start_soon(partial(param._check, **params))

        if self.params:
            async with anyio.create_task_group() as tg:
                for param in self.params:
                    tg.start_soon(
                        partial(cast(Param, param.field_info)._check, **params)
                    )

    async def _solve_field(
        self, field: ModelField, params: dict[str, Any]
    ) -> Any:
        param = cast(Param, field.field_info)
        value = await param._solve(**params)
        if value is PydanticUndefined:
            value = field.get_default()
        v = check_field_type(field, value)
        return v if param.validate else value

    async def solve(self, **params: Any) -> dict[str, Any]:
        """Resolve dependencies and validate their annotated values."""
        for param in self.parameterless:
            await param._solve(**params)

        # solve param values
        result: dict[str, Any] = {}
        if not self.params:
            return result

        async def _solve_field(
            field: ModelField, params: dict[str, Any]
        ) -> None:
            value = await self._solve_field(field, params)
            result[field.name] = value

        async with anyio.create_task_group() as tg:
            for field in self.params:
                # shield the task to prevent cancellation
                # when one of the tasks raises an exception
                # this will improve the dependency cache reusability
                tg.start_soon(run_coro_with_shield, _solve_field(field, params))

        return result


__autodoc__ = {"CustomConfig": False}
