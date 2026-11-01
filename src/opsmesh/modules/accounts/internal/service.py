"""Password authentication, revocable sessions and role grants."""

import asyncio
import hashlib
import re
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from uuid import NAMESPACE_URL, uuid4, uuid5

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import AccessRole, AccessToken, DepartmentProfile, UserPrincipal

_OWN_ACTIONS = frozenset(
    {
        "session:read",
        "session:write",
        "run:read",
        "run:submit",
        "run:cancel",
        "run:approve",
        "agent:read",
        "agent:write",
        "agent:publish",
        "workspace:read",
        "workspace:write",
        "memory:read",
        "memory:write",
    }
)


class LocalAccountService:
    def __init__(self, database: AsyncDatabase, *, token_ttl: int = 3600) -> None:
        self.database = database
        self.users = VersionedRecordStore(database, "accounts_users")
        self.roles = VersionedRecordStore(database, "accounts_roles")
        self.departments = VersionedRecordStore(database, "accounts_departments")
        self.tokens = VersionedRecordStore(database, "accounts_tokens")
        self.hasher = PasswordHasher()
        self.token_ttl = token_ttl

    @staticmethod
    def _user_id(username: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"opsmesh:users:{username.casefold()}"))

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        is_admin: bool = False,
        roles: tuple[str, ...] = (),
        departments: tuple[str, ...] = (),
    ) -> UserPrincipal:
        username = username.casefold()
        if not re.fullmatch(r"[a-z][a-z0-9._-]{2,63}", username) or not 12 <= len(password) <= 1024:
            raise OpsMeshError(
                "invalid_request", "Use a valid username and a 12-1024 character password"
            )
        await self._validate_memberships(roles, departments)
        principal = UserPrincipal(
            id=self._user_id(username),
            username=username,
            is_admin=is_admin,
            roles=roles,
            departments=departments,
        )
        digest = await asyncio.to_thread(self.hasher.hash, password)
        await self.users.create(
            principal.id,
            {**principal.model_dump(mode="json"), "password_hash": digest},
            events=[{"type": "accounts.user.created", "subject": principal.id}],
        )
        return principal

    async def _validate_memberships(
        self, roles: tuple[str, ...], departments: tuple[str, ...]
    ) -> None:
        for role in roles:
            if await self.roles.get(role) is None:
                raise OpsMeshError("not_found", "AccessRole does not exist")
        for department in departments:
            if await self.departments.get(department) is None:
                raise OpsMeshError("not_found", "DepartmentProfile does not exist")

    async def get_principal(self, principal_id: str) -> UserPrincipal:
        row = await self.users.get(principal_id)
        if row is None:
            raise OpsMeshError("not_found", "User does not exist")
        return UserPrincipal.model_validate(
            {
                **{
                    k: v for k, v in row.data.items() if k not in {"password_hash", "session_epoch"}
                },
                "revision": row.revision,
            }
        )

    async def list_users(self) -> list[UserPrincipal]:
        return [
            UserPrincipal.model_validate(
                {
                    **{
                        k: v
                        for k, v in row.data.items()
                        if k not in {"password_hash", "session_epoch"}
                    },
                    "revision": row.revision,
                }
            )
            for row in await self.users.list()
        ]

    async def update_user(
        self,
        principal_id: str,
        *,
        active: bool | None = None,
        roles: tuple[str, ...] | None = None,
        departments: tuple[str, ...] | None = None,
    ) -> UserPrincipal:
        row = await self.users.get(principal_id)
        if row is None:
            raise OpsMeshError("not_found", "User does not exist")
        await self._validate_memberships(roles or (), departments or ())
        data = dict(row.data)
        for key, value in (("active", active), ("roles", roles), ("departments", departments)):
            if value is not None:
                data[key] = list(value) if isinstance(value, tuple) else value
        await self.users.update(
            principal_id,
            data,
            row.revision,
            events=[{"type": "accounts.user.updated", "subject": principal_id}],
        )
        return await self.get_principal(principal_id)

    async def create_role(self, role: AccessRole) -> AccessRole:
        await self.roles.create(role.name, role.model_dump(mode="json"))
        return role

    async def list_roles(self) -> list[AccessRole]:
        return [AccessRole.model_validate(row.data) for row in await self.roles.list()]

    async def create_department(self, name: str, parent_id: str | None = None) -> DepartmentProfile:
        if not name.strip():
            raise OpsMeshError("invalid_request", "DepartmentProfile name is required")
        if parent_id and await self.departments.get(parent_id) is None:
            raise OpsMeshError("not_found", "Parent department does not exist")
        department = DepartmentProfile(id=str(uuid4()), name=name, parent_id=parent_id)
        await self.departments.create(department.id, department.model_dump(mode="json"))
        return department

    async def list_departments(self) -> list[DepartmentProfile]:
        return [DepartmentProfile.model_validate(row.data) for row in await self.departments.list()]

    async def login(self, username: str, password: str) -> AccessToken:
        row = await self.users.get(self._user_id(username))
        valid = False
        if row is not None:
            with suppress(VerificationError, InvalidHashError):
                valid = await asyncio.to_thread(
                    self.hasher.verify, str(row.data["password_hash"]), password
                )
        if not valid or row is None or not row.data["active"]:
            raise OpsMeshError("unauthenticated", "Invalid credentials")
        token = secrets.token_urlsafe(48)
        await self.tokens.create(
            hashlib.sha256(token.encode()).hexdigest(),
            {
                "principal_id": row.id,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=self.token_ttl)).isoformat(),
                "session_epoch": row.data.get("session_epoch", 0),
            },
        )
        return AccessToken(
            token=token, expires_in=self.token_ttl, principal=await self.get_principal(row.id)
        )

    async def authenticate(self, token: str) -> UserPrincipal:
        row = await self.tokens.get(hashlib.sha256(token.encode()).hexdigest())
        if row is None or datetime.fromisoformat(row.data["expires_at"]) <= datetime.now(UTC):
            raise OpsMeshError("unauthenticated", "Session is missing or expired")
        principal = await self.get_principal(row.data["principal_id"])
        user = await self.users.get(principal.id)
        if user is None or row.data.get("session_epoch", 0) != user.data.get("session_epoch", 0):
            raise OpsMeshError("UNAUTHENTICATED", "Session has been revoked")
        if not principal.active:
            raise OpsMeshError("unauthenticated", "User is disabled")
        return principal

    async def logout(self, token: str) -> None:
        key = hashlib.sha256(token.encode()).hexdigest()
        row = await self.tokens.get(key)
        if row is not None:
            await self.tokens.delete(key, row.revision)

    async def authorize(
        self, principal: UserPrincipal, action: str, resource: str, *, owner_id: str | None = None
    ) -> None:
        principal = await self.get_principal(principal.id)
        if not principal.active:
            raise OpsMeshError("forbidden", "User is disabled")
        if principal.is_admin or (owner_id == principal.id and action in _OWN_ACTIONS):
            return
        for role_name in principal.roles:
            row = await self.roles.get(role_name)
            if row is None:
                continue
            for grant in AccessRole.model_validate(row.data).grants:
                if (
                    fnmatchcase(action, grant.action)
                    and fnmatchcase(resource, grant.resource)
                    and (grant.department is None or grant.department in principal.departments)
                ):
                    return
        raise OpsMeshError("forbidden", "The operation is not permitted")
