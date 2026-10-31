"""Signature inspection without a provider registry."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from opsmesh.framework.exception import DependencyError


def typed_signature(call: Callable[..., Any]) -> inspect.Signature:
    target: Any = call
    if not inspect.isfunction(call) and not inspect.isclass(call):
        target = type(call).__call__
    try:
        hints = get_type_hints(target, include_extras=True)
    except (NameError, TypeError) as exc:
        raise DependencyError(f"Cannot resolve annotations for {call}: {exc}") from exc
    signature = inspect.signature(call)
    parameters = []
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.kind == parameter.POSITIONAL_ONLY:
            raise DependencyError("Injected callables cannot use positional-only parameters")
        parameters.append(
            parameter.replace(annotation=hints.get(parameter.name, parameter.annotation))
        )
    return signature.replace(parameters=parameters)


def unwrap(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0], args[1:]
    return annotation, ()
