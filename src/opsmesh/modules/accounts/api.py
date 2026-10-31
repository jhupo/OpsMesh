"""Local identity and authorization contracts."""

from datetime import datetime
from typing import Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class UserPrincipal(OpsMeshContract):
    id: str
    username: str
    roles: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()
    is_admin: bool = False
    active: bool = True
    revision: int = 1


class PermissionGrant(OpsMeshContract):
    action: str
    resource: str = "*"
    department: str | None = None


class AccessRole(OpsMeshContract):
    name: str = Field(min_length=1, max_length=100)
    grants: tuple[PermissionGrant, ...] = ()
    revision: int = 1


class DepartmentProfile(OpsMeshContract):
    id: str
    name: str
    parent_id: str | None = None
    revision: int = 1


class LoginSession(OpsMeshContract):
    id: str
    principal_id: str
    created_at: datetime
    expires_at: datetime


class AccountManagement(Protocol):
    """Actor-aware account use cases for all user-facing entry points."""

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        actor_id: str,
        is_admin: bool = False,
        roles: tuple[str, ...] = (),
        departments: tuple[str, ...] = (),
    ) -> UserPrincipal: ...
    async def get_principal(self, principal_id: str, *, actor_id: str) -> UserPrincipal: ...
    async def list_users(self, *, actor_id: str) -> list[UserPrincipal]: ...
    async def update_user(
        self,
        principal_id: str,
        *,
        actor_id: str,
        expected_revision: int | None = None,
        active: bool | None = None,
        roles: tuple[str, ...] | None = None,
        departments: tuple[str, ...] | None = None,
    ) -> UserPrincipal: ...
    async def create_role(self, role: AccessRole, *, actor_id: str) -> AccessRole: ...
    async def get_role(self, name: str, *, actor_id: str) -> AccessRole: ...
    async def list_roles(self, *, actor_id: str) -> list[AccessRole]: ...
    async def update_role(
        self, role: AccessRole, *, actor_id: str, expected_revision: int
    ) -> AccessRole: ...
    async def delete_role(self, name: str, *, actor_id: str, expected_revision: int) -> None: ...
    async def create_department(
        self, name: str, parent_id: str | None = None, *, actor_id: str
    ) -> DepartmentProfile: ...
    async def get_department(self, department_id: str, *, actor_id: str) -> DepartmentProfile: ...
    async def list_departments(self, *, actor_id: str) -> list[DepartmentProfile]: ...
    async def update_department(
        self, department: DepartmentProfile, *, actor_id: str, expected_revision: int
    ) -> DepartmentProfile: ...
    async def delete_department(
        self, department_id: str, *, actor_id: str, expected_revision: int
    ) -> None: ...
    async def change_password(
        self, current_password: str, new_password: str, *, actor_id: str
    ) -> None: ...
    async def reset_password(
        self, principal_id: str, new_password: str, *, actor_id: str
    ) -> None: ...
    async def list_sessions(self, principal_id: str, *, actor_id: str) -> list[LoginSession]: ...
    async def revoke_sessions(self, principal_id: str, *, actor_id: str) -> None: ...
    async def revoke_session(
        self, principal_id: str, session_id: str, *, actor_id: str
    ) -> None: ...


class AccessToken(OpsMeshContract):
    token: str
    expires_in: int
    principal: UserPrincipal


class AccountDirectory(Protocol):
    async def create_user(
        self,
        username: str,
        password: str,
        *,
        is_admin: bool = False,
        roles: tuple[str, ...] = (),
        departments: tuple[str, ...] = (),
    ) -> UserPrincipal: ...
    async def list_users(self) -> list[UserPrincipal]: ...
    async def get_principal(self, principal_id: str) -> UserPrincipal: ...
    async def update_user(
        self,
        principal_id: str,
        *,
        active: bool | None = None,
        roles: tuple[str, ...] | None = None,
        departments: tuple[str, ...] | None = None,
    ) -> UserPrincipal: ...
    async def create_role(self, role: AccessRole) -> AccessRole: ...
    async def list_roles(self) -> list[AccessRole]: ...
    async def create_department(
        self, name: str, parent_id: str | None = None
    ) -> DepartmentProfile: ...
    async def list_departments(self) -> list[DepartmentProfile]: ...
    async def login(self, username: str, password: str) -> AccessToken: ...
    async def authenticate(self, token: str) -> UserPrincipal: ...
    async def logout(self, token: str) -> None: ...
    async def authorize(
        self, principal: UserPrincipal, action: str, resource: str, *, owner_id: str | None = None
    ) -> None: ...
