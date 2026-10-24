from fastapi import APIRouter, Depends, Request
from opsmesh_sdk.identity import UserIdentity
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ForbiddenError, NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.shared.models import AuditEvent

from .auth import login
from .models import AccessToken, Group, GroupMember, User
from .passwords import hash_password
from .schemas import (
    AccessTokenView,
    ChangePasswordRequest,
    CreateAccessTokenRequest,
    CreateGroupRequest,
    GroupView,
    IssuedAccessTokenView,
    LoginRequest,
    SaveUserRequest,
    UserIdentityView,
    UserView,
)
from .service import issue_token, require_admin, require_owner

router = APIRouter(tags=["users"])


@router.post("/auth/login", response_model=IssuedAccessTokenView)
def password_login(body: LoginRequest, request: Request) -> dict[str, str]:
    app = get_application(request)
    with app.sessions.begin() as db:
        credential = login(
            db,
            body.username,
            body.password.get_secret_value(),
            request.client.host if request.client else "unknown",
        )
        if credential is not None:
            db.add(
                AuditEvent(
                    user_id=None,
                    request_id=request.state.request_id,
                    method="POST",
                    route="/v1/auth/login",
                    status=200,
                )
            )
    if credential is None:
        raise ForbiddenError("Invalid credentials or login limit exceeded")
    return {"token": credential}


@router.put("/users/{user_id}/password", status_code=204)
def change_password(
    user_id: str,
    body: ChangePasswordRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_owner(user, user_id)
    row = db.get(User, user_id, with_for_update=True)
    if row is None:
        raise NotFoundError("User not found")
    row.password_hash = hash_password(body.password.get_secret_value())
    db.execute(update(AccessToken).where(AccessToken.user_id == user_id).values(revoked=True))


@router.get("/users/me", response_model=UserIdentityView)
def me(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
    return user


@router.get("/users", response_model=list[UserView])
def users(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict]:
    require_admin(user)
    return [
        serialize_record(row) for row in db.scalars(select(User).order_by(User.name).limit(200))
    ]


@router.post("/users", status_code=201, response_model=UserView)
def create_user(
    body: SaveUserRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict:
    require_admin(user)
    row = User(**body.model_dump(exclude={"password"}))
    if body.password is not None:
        row.password_hash = hash_password(body.password.get_secret_value())
    db.add(row)
    db.flush()
    return serialize_record(row)


@router.put("/users/{user_id}", response_model=UserView)
def update_user(
    user_id: str,
    body: SaveUserRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict:
    require_admin(user)
    row = db.get(User, user_id, with_for_update=True)
    if row is None:
        raise NotFoundError("User not found")
    for key, value in body.model_dump(exclude={"password"}).items():
        setattr(row, key, value)
    if body.password is not None:
        row.password_hash = hash_password(body.password.get_secret_value())
        db.execute(update(AccessToken).where(AccessToken.user_id == user_id).values(revoked=True))
    return serialize_record(row)


@router.post("/users/{user_id}/tokens", status_code=201, response_model=IssuedAccessTokenView)
def create_token(
    user_id: str,
    body: CreateAccessTokenRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict:
    require_owner(user, user_id)
    return {"token": issue_token(db, user_id, body.label, body.expires_at)}


@router.get("/users/{user_id}/tokens", response_model=list[AccessTokenView])
def tokens(
    user_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict]:
    require_owner(user, user_id)
    return [
        serialize_record(row)
        for row in db.scalars(select(AccessToken).where(AccessToken.user_id == user_id).limit(100))
    ]


@router.delete("/users/{user_id}/tokens/{token_id}", status_code=204)
def revoke_token(
    user_id: str,
    token_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_owner(user, user_id)
    row = db.get(AccessToken, token_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("Token not found")
    row.revoked = True


@router.get("/groups", response_model=list[GroupView])
def groups(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict]:
    query = select(Group)
    if not user.is_admin:
        query = query.join(GroupMember).where(GroupMember.user_id == user.user_id)
    return [serialize_record(row) for row in db.scalars(query.order_by(Group.name).limit(200))]


@router.post("/groups", status_code=201, response_model=GroupView)
def create_group(
    body: CreateGroupRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict:
    require_admin(user)
    row = Group(name=body.name)
    db.add(row)
    db.flush()
    return serialize_record(row)


@router.get("/groups/{group_id}/members", response_model=list[UserView])
def members(
    group_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict]:
    if not user.is_admin and group_id not in user.group_ids:
        raise NotFoundError("Group not found")
    return [
        serialize_record(row)
        for row in db.scalars(
            select(User).join(GroupMember).where(GroupMember.group_id == group_id).limit(200)
        )
    ]


@router.put("/groups/{group_id}/members/{user_id}", status_code=204)
def add_member(
    group_id: str,
    user_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_admin(user)
    if db.get(Group, group_id) is None or db.get(User, user_id) is None:
        raise NotFoundError("Group or user not found")
    if db.get(GroupMember, (group_id, user_id)) is None:
        db.add(GroupMember(group_id=group_id, user_id=user_id))


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
def remove_member(
    group_id: str,
    user_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_admin(user)
    db.execute(
        delete(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
