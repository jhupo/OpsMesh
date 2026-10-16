"""User CRUD, current profile and avatar APIs; authentication reuses identity.auth."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Header, Query, Response, status
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users import service
from opsmesh.identity.users.models import UserProfile
from opsmesh.identity.users.schemas import (
    AdminUpdateUserRequest,
    UpdateProfileRequest,
    UserPage,
    UserProfileView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(tags=["identity"])


def _view(profile: UserProfile) -> UserProfileView:
    return UserProfileView(
        user_id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        status=profile.status,
        platform_roles=sorted(profile.platform_roles),
        work_root=profile.work_root,
        has_avatar=profile.avatar_content is not None,
        avatar_content_type=profile.avatar_content_type,
        avatar_updated_at=profile.avatar_updated_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _readable_profile(user_id: UUID, session: Session, reader: CurrentUser) -> UserProfile:
    """An account reads itself; anything else needs the platform admin role."""
    if reader.user_id == user_id:
        return service.get_user(session, user_id)
    require_platform_admin(reader)
    return service.get_user(session, user_id)


@router.get("/users/me", response_model=UserProfileView, operation_id="identity.users.read_current")
def read_current_profile(session: DbSession, user: CurrentUserDep) -> UserProfileView:
    return _view(service.get_user(session, user.user_id))


@router.patch(
    "/users/me", response_model=UserProfileView, operation_id="identity.users.update_current"
)
def update_current_profile(
    payload: UpdateProfileRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> UserProfileView:
    with session_transaction(session):
        profile = service.update_profile(
            session,
            user_id=user.user_id,
            actor=AuditActor.from_user(user),
            display_name=payload.display_name,
            work_root=payload.work_root,
        )
    return _view(profile)


@router.get("/users", response_model=UserPage, operation_id="identity.users.list")
def list_users(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    email_query: Annotated[str | None, Query(max_length=320)] = None,
) -> UserPage:
    require_platform_admin(user)
    page = PageParams(limit=limit, offset=offset)
    profiles, total = service.list_users(
        session, page=page, status=status_filter, email_query=email_query
    )
    return UserPage(users=[_view(profile) for profile in profiles], total=total)


@router.get("/users/{user_id}", response_model=UserProfileView, operation_id="identity.users.read")
def read_user(user_id: UUID, session: DbSession, user: CurrentUserDep) -> UserProfileView:
    return _view(_readable_profile(user_id, session, user))


@router.patch(
    "/users/{user_id}", response_model=UserProfileView, operation_id="identity.users.update"
)
def admin_update_user(
    user_id: UUID,
    payload: AdminUpdateUserRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> UserProfileView:
    require_platform_admin(user)
    with session_transaction(session):
        # One service call owns the whole update and its lock order; calling
        # the field setters one by one would let a combined request take the
        # audit chain head before the administrator rows a roles-only request
        # already holds.
        profile = service.apply_admin_update(
            session,
            user_id=user_id,
            actor=AuditActor.from_user(user),
            display_name=payload.display_name,
            work_root=payload.work_root,
            status=payload.status,
            roles=(
                frozenset(payload.platform_roles)
                if payload.platform_roles is not None
                else None
            ),
        )
    return _view(profile)


@router.put(
    "/users/me/avatar",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="identity.users.avatar.upload",
)
def upload_current_avatar(
    session: DbSession,
    user: CurrentUserDep,
    content_type: Annotated[str, Header(alias="Content-Type")],
    body: Annotated[bytes, Body()],
) -> Response:
    with session_transaction(session):
        service.set_avatar(
            session,
            user_id=user.user_id,
            content=body,
            content_type=content_type,
            actor=AuditActor.from_user(user),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/me/avatar", operation_id="identity.users.avatar.read")
def read_current_avatar(session: DbSession, user: CurrentUserDep) -> Response:
    profile = service.get_user(session, user.user_id)
    if profile.avatar_content is None or profile.avatar_content_type is None:
        raise NotFoundError("The account has no avatar")
    return Response(
        content=profile.avatar_content,
        media_type=profile.avatar_content_type,
        headers={"Cache-Control": "no-store"},
    )


@router.delete(
    "/users/me/avatar",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="identity.users.avatar.delete",
)
def delete_current_avatar(session: DbSession, user: CurrentUserDep) -> Response:
    with session_transaction(session):
        service.clear_avatar(session, user_id=user.user_id, actor=AuditActor.from_user(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
