"""Declare plugin routes and derive their request/response contracts."""

import inspect
from collections.abc import Callable
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, TypeAdapter
from starlette.responses import Response

from opsmesh.matchers import Matcher
from opsmesh.permissions import Permission
from opsmesh.rules import Rule

from .errors import RouteRegistrationError
from .routes import ACCESS_INVOKE, Access, APIRouter

_router = APIRouter()


def get_router() -> APIRouter:
    """Return the registry of explicitly declared plugin routes."""
    return _router


def _return_annotation(handler: Callable[..., Any]) -> Any:
    try:
        annotations = get_type_hints(handler, include_extras=True)
    except (NameError, TypeError) as error:
        raise RouteRegistrationError(
            "Route annotations cannot be resolved"
        ) from error
    annotation = annotations.get("return", inspect.Signature.empty)
    if annotation is inspect.Signature.empty:
        raise RouteRegistrationError(
            "Route handlers must declare a return type"
        )
    return annotation


def _response_model(annotation: Any) -> type[BaseModel] | None:
    if annotation is inspect.Signature.empty:
        return None
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def route(
    method: str,
    path: str,
    *,
    access: Access = ACCESS_INVOKE,
    name: str | None = None,
    body: type[BaseModel] | None = None,
    rule: Rule | None = None,
    permission: Permission | None = None,
    message_matchers: tuple[type[Matcher], ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare an event handler with an optional JSON validation contract.

    The body model validates input before event dispatch. Handlers consume
    framework Event, EventMessage(), and Context() dependencies; response
    validation follows the return annotation.
    """

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        annotation = _return_annotation(handler)
        if get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
        response = _response_model(annotation)
        allows_response = annotation is Response or Response in get_args(
            annotation
        )
        if annotation is Response:
            adapter = None
        elif response is not None:
            adapter = TypeAdapter(annotation)
        else:
            adapter = TypeAdapter(
                annotation, config=ConfigDict(arbitrary_types_allowed=True)
            )
        _router.register(
            method=method,
            path=path,
            handler=handler,
            body=body,
            response_adapter=adapter,
            allow_response=allows_response,
            access=access,
            name=name,
            rule=rule,
            permission=permission,
            message_matchers=message_matchers,
        )
        return handler

    return decorator


def get(
    path: str, **options: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a plugin-owned GET route."""
    return route("GET", path, **options)


def post(
    path: str, **options: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a plugin-owned POST route."""
    return route("POST", path, **options)


def put(
    path: str, **options: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a plugin-owned PUT route."""
    return route("PUT", path, **options)


def patch(
    path: str, **options: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a plugin-owned PATCH route."""
    return route("PATCH", path, **options)


def delete(
    path: str, **options: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a plugin-owned DELETE route."""
    return route("DELETE", path, **options)
