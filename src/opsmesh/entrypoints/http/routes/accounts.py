"""Local account and session authentication routes."""

from fastapi import APIRouter, Request, Response
from pydantic import Field, SecretStr

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.accounts.api import (
    AccessRole,
    AccessToken,
    DepartmentProfile,
    LoginSession,
    UserPrincipal,
)

from ..security import CurrentUser, get_application

router = APIRouter(tags=["accounts"])


class LoginRequest(OpsMeshContract):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr


class CreateUserRequest(LoginRequest):
    is_admin: bool = False
    roles: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()


class UpdateUserRequest(OpsMeshContract):
    expected_revision: int | None = Field(default=None, ge=1)
    active: bool | None = None
    roles: tuple[str, ...] | None = None
    departments: tuple[str, ...] | None = None


class CreateDepartmentRequest(OpsMeshContract):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class UpdateRoleRequest(OpsMeshContract):
    role: AccessRole
    expected_revision: int = Field(ge=1)


class UpdateDepartmentRequest(CreateDepartmentRequest):
    expected_revision: int = Field(ge=1)


class ChangePasswordRequest(OpsMeshContract):
    current_password: SecretStr
    new_password: SecretStr


class ResetPasswordRequest(OpsMeshContract):
    new_password: SecretStr


@router.post("/auth/login", response_model=AccessToken)
async def login(body: LoginRequest, request: Request) -> AccessToken:
    app = get_application(request)
    address = request.client.host if request.client else "unknown"
    await app.services.rate_limiter.check(
        f"login:{address}", limit=app.settings.login_requests_per_minute
    )
    token = await app.services.accounts.login(body.username, body.password.get_secret_value())
    request.state.principal_id = token.principal.id
    return token


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, user: CurrentUser) -> Response:
    await get_application(request).services.accounts.logout(request.state.bearer_token)
    return Response(status_code=204)


@router.get("/auth/me")
async def me(user: CurrentUser) -> UserPrincipal:
    return user


@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserRequest, request: Request, user: CurrentUser
) -> UserPrincipal:
    return await get_application(request).services.account_management.create_user(
        body.username,
        body.password.get_secret_value(),
        is_admin=body.is_admin,
        roles=body.roles,
        departments=body.departments,
        actor_id=user.id,
    )


@router.get("/users")
async def users(request: Request, user: CurrentUser) -> list[UserPrincipal]:
    return await get_application(request).services.account_management.list_users(actor_id=user.id)


@router.get("/users/{principal_id}")
async def get_user(principal_id: str, request: Request, user: CurrentUser) -> UserPrincipal:
    return await get_application(request).services.account_management.get_principal(
        principal_id, actor_id=user.id
    )


@router.patch("/users/{principal_id}")
async def update_user(
    principal_id: str, body: UpdateUserRequest, request: Request, user: CurrentUser
) -> UserPrincipal:
    return await get_application(request).services.account_management.update_user(
        principal_id, actor_id=user.id, **body.model_dump()
    )


@router.post("/roles", status_code=201)
async def create_role(body: AccessRole, request: Request, user: CurrentUser) -> AccessRole:
    return await get_application(request).services.account_management.create_role(
        body, actor_id=user.id
    )


@router.get("/roles")
async def roles(request: Request, user: CurrentUser) -> list[AccessRole]:
    return await get_application(request).services.account_management.list_roles(actor_id=user.id)


@router.post("/departments", status_code=201)
async def create_department(
    body: CreateDepartmentRequest, request: Request, user: CurrentUser
) -> DepartmentProfile:
    return await get_application(request).services.account_management.create_department(
        body.name, body.parent_id, actor_id=user.id
    )


@router.get("/departments")
async def departments(request: Request, user: CurrentUser) -> list[DepartmentProfile]:
    return await get_application(request).services.account_management.list_departments(
        actor_id=user.id
    )


@router.get("/roles/{name}")
async def get_role(name: str, request: Request, user: CurrentUser) -> AccessRole:
    return await get_application(request).services.account_management.get_role(
        name, actor_id=user.id
    )


@router.put("/roles/{name}")
async def update_role(
    name: str, body: UpdateRoleRequest, request: Request, user: CurrentUser
) -> AccessRole:
    from opsmesh.framework.errors import OpsMeshError

    if name != body.role.name:
        raise OpsMeshError("INVALID_REQUEST", "Role name cannot change")
    return await get_application(request).services.account_management.update_role(
        body.role, expected_revision=body.expected_revision, actor_id=user.id
    )


@router.delete("/roles/{name}", status_code=204)
async def delete_role(
    name: str, expected_revision: int, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.account_management.delete_role(
        name, expected_revision=expected_revision, actor_id=user.id
    )
    return Response(status_code=204)


@router.get("/departments/{department_id}")
async def get_department(
    department_id: str, request: Request, user: CurrentUser
) -> DepartmentProfile:
    return await get_application(request).services.account_management.get_department(
        department_id, actor_id=user.id
    )


@router.put("/departments/{department_id}")
async def update_department(
    department_id: str,
    body: UpdateDepartmentRequest,
    request: Request,
    user: CurrentUser,
) -> DepartmentProfile:
    return await get_application(request).services.account_management.update_department(
        DepartmentProfile(id=department_id, name=body.name, parent_id=body.parent_id),
        expected_revision=body.expected_revision,
        actor_id=user.id,
    )


@router.delete("/departments/{department_id}", status_code=204)
async def delete_department(
    department_id: str,
    expected_revision: int,
    request: Request,
    user: CurrentUser,
) -> Response:
    await get_application(request).services.account_management.delete_department(
        department_id, expected_revision=expected_revision, actor_id=user.id
    )
    return Response(status_code=204)


@router.post("/auth/password", status_code=204)
async def change_password(
    body: ChangePasswordRequest, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.account_management.change_password(
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
        actor_id=user.id,
    )
    return Response(status_code=204)


@router.post("/users/{principal_id}/password/reset", status_code=204)
async def reset_password(
    principal_id: str,
    body: ResetPasswordRequest,
    request: Request,
    user: CurrentUser,
) -> Response:
    await get_application(request).services.account_management.reset_password(
        principal_id, body.new_password.get_secret_value(), actor_id=user.id
    )
    return Response(status_code=204)


@router.get("/users/{principal_id}/sessions")
async def login_sessions(
    principal_id: str, request: Request, user: CurrentUser
) -> list[LoginSession]:
    return await get_application(request).services.account_management.list_sessions(
        principal_id, actor_id=user.id
    )


@router.delete("/users/{principal_id}/sessions", status_code=204)
async def revoke_sessions(principal_id: str, request: Request, user: CurrentUser) -> Response:
    await get_application(request).services.account_management.revoke_sessions(
        principal_id, actor_id=user.id
    )
    return Response(status_code=204)


@router.delete("/users/{principal_id}/sessions/{session_id}", status_code=204)
async def revoke_session(
    principal_id: str,
    session_id: str,
    request: Request,
    user: CurrentUser,
) -> Response:
    await get_application(request).services.account_management.revoke_session(
        principal_id, session_id, actor_id=user.id
    )
    return Response(status_code=204)
