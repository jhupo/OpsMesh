"""Account profile, avatar and administrator user APIs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Header, Query, Request, Response, status

from opsmesh.platform.audit.contracts import AuditActor
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.users import service
from opsmesh.users.access import require_platform_admin
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.users.schemas import (
    AdminCreateUserRequest,
    AdminUpdateUserRequest,
    UpdateProfileRequest,
    UserPage,
    UserProfileView,
    profile_view,
)

router = APIRouter(tags=["users"])


@router.get("/users/me", response_model=UserProfileView, operation_id="users.read_current")
def read_current_profile(session: DbSession, user: CurrentUserDep) -> UserProfileView:
    return profile_view(service.get_user(session, user.user_id))


@router.patch("/users/me", response_model=UserProfileView, operation_id="users.update_current")
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
        )
    return profile_view(profile)


@router.put(
    "/users/me/avatar",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="users.avatar.upload",
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


@router.get("/users/me/avatar", operation_id="users.avatar.read")
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
    operation_id="users.avatar.delete",
)
def delete_current_avatar(session: DbSession, user: CurrentUserDep) -> Response:
    with session_transaction(session):
        service.clear_avatar(session, user_id=user.user_id, actor=AuditActor.from_user(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users", status_code=201, response_model=UserProfileView, operation_id="users.create")
def create_user(
    payload: AdminCreateUserRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> UserProfileView:
    require_platform_admin(user)
    with session_transaction(session):
        profile = service.create_account(
            session,
            email=str(payload.email),
            display_name=payload.display_name,
            password=payload.password.get_secret_value(),
            platform_roles=frozenset(payload.platform_roles),
            token_limit=payload.token_limit,
            actor=AuditActor.from_user(user),
            capacity_limits={
                "concurrency": request.app.state.settings.default_user_concurrency_limit,
                "environment": request.app.state.settings.default_user_environment_limit,
            },
        )
    return profile_view(profile)


@router.get("/users", response_model=UserPage, operation_id="users.list")
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
    return UserPage(users=[profile_view(profile) for profile in profiles], total=total)


@router.get("/users/{user_id}", response_model=UserProfileView, operation_id="users.read")
def read_user(user_id: UUID, session: DbSession, user: CurrentUserDep) -> UserProfileView:
    require_platform_admin(user)
    return profile_view(service.get_user(session, user_id))


@router.patch("/users/{user_id}", response_model=UserProfileView, operation_id="users.update")
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
            status=payload.status,
            roles=(
                frozenset(payload.platform_roles) if payload.platform_roles is not None else None
            ),
        )
    return profile_view(profile)


@router.delete("/users/{user_id}", status_code=204, operation_id="users.delete")
def delete_user(user_id: UUID, session: DbSession, user: CurrentUserDep) -> Response:
    """Disable access permanently; referenced resources are retained for later cleanup."""
    require_platform_admin(user)
    with session_transaction(session):
        service.delete_account(session, user_id=user_id, actor=AuditActor.from_user(user))
    return Response(status_code=204)
