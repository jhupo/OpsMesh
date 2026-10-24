from collections.abc import Iterator
from typing import Any

from fastapi import Depends, Header, Request
from opsmesh_sdk.identity import UserIdentity
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.models import AuditEvent
from opsmesh.users.service import authenticate


def get_application(request: Request) -> Any:
    return request.app.state.application


def get_db_session(request: Request) -> Iterator[Session]:
    with get_application(request).sessions.begin() as value:
        yield value
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            route = request.scope.get("route")
            value.add(
                AuditEvent(
                    user_id=getattr(request.state, "user_id", None),
                    request_id=request.state.request_id,
                    method=request.method,
                    route=getattr(route, "path", "unmatched"),
                    status=getattr(route, "status_code", None) or 200,
                )
            )


def get_current_user(
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db_session, scope="function"),
) -> UserIdentity:
    if not authorization.startswith("Bearer "):
        raise ForbiddenError("A bearer token is required")
    user = authenticate(db, authorization[7:])
    request.state.user_id = user.user_id
    return user


def serialize_record(row: Any, *, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        column.key: getattr(row, column.key)
        for column in inspect(type(row)).columns
        if column.key not in exclude
        and not column.key.startswith("encrypted_")
        and column.key not in {"digest", "password_hash"}
    }
