"""Resolve callable annotations and validate dependency values."""

import inspect
import logging
from collections.abc import Callable
from typing import Any, ForwardRef, cast

from typing_extensions import TypeAliasType

from opsmesh.compat import ModelField
from opsmesh.exceptions import TypeMisMatch
from opsmesh.typing import evaluate_forwardref, is_type_alias_type

logger = logging.getLogger(__name__)


def get_typed_signature(call: Callable[..., Any]) -> inspect.Signature:
    """Return a callable signature with resolved type annotations."""
    signature = inspect.signature(call)
    globalns = getattr(call, "__globals__", {})
    typed_params = [
        inspect.Parameter(
            name=param.name,
            kind=param.kind,
            default=param.default,
            annotation=get_typed_annotation(param, globalns),
        )
        for param in signature.parameters.values()
    ]
    return inspect.Signature(typed_params)


def get_typed_annotation(
    param: inspect.Parameter, globalns: dict[str, Any]
) -> Any:
    """Resolve a parameter's type annotation."""
    annotation = param.annotation
    if isinstance(annotation, str):
        annotation = ForwardRef(annotation)
        try:
            annotation = evaluate_forwardref(annotation, globalns, globalns)
        except Exception as e:
            logger.warning(
                "Cannot resolve annotation for parameter %s",
                param.name,
                exc_info=(type(e), e, e.__traceback__),
            )
            return inspect.Parameter.empty
    if is_type_alias_type(annotation):
        # Python 3.12+ supports PEP 695 TypeAliasType
        annotation = cast(TypeAliasType, annotation).__value__
    return annotation


def check_field_type(field: ModelField, value: Any) -> Any:
    """Validate a value against its field annotation."""
    try:
        return field.validate_value(value)
    except ValueError as error:
        raise TypeMisMatch(field, value) from error
