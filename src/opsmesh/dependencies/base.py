"""Base contract for dependency parameter descriptors."""

import abc
import inspect
from typing import Any

from opsmesh.compat import FieldInfo


class Param(abc.ABC, FieldInfo):
    """A parameter descriptor used by dependency injection.

    Extends Pydantic FieldInfo with resolution behavior, excluding the name.
    """

    def __init__(self, *args, validate: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.validate = validate

    @classmethod
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type["Param"], ...]
    ) -> "Param | None":
        return

    @classmethod
    def _check_parameterless(
        cls, value: Any, allow_types: tuple[type["Param"], ...]
    ) -> "Param | None":
        return

    @abc.abstractmethod
    async def _solve(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def _check(self, **kwargs: Any) -> None:
        return
