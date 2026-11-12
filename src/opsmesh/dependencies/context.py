"""Inject host-owned invocation context without exposing transport types."""

import inspect
from typing import Annotated, Any, get_args, get_origin

from .base import Param


class ContextParam(Param):
    """Resolve context supplied explicitly by a callable invocation owner."""

    @classmethod
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> "ContextParam | None":
        if get_origin(param.annotation) is Annotated:
            for marker in get_args(param.annotation)[1:]:
                if isinstance(marker, cls):
                    return marker
        return None

    async def _solve(self, *, invocation_context: Any, **kwargs: Any) -> Any:
        return invocation_context


def Context() -> Any:
    """Mark a dependency parameter for the host's invocation context."""
    return ContextParam()
