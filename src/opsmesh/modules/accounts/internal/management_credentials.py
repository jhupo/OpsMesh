"""Password rotation and revocable login sessions; bearer tokens never leave storage."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from argon2.exceptions import InvalidHashError, VerificationError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import LoginSession, UserPrincipal

from .management_base import AccountManagementBase
from .management_policy import principal_view
from .transactions import AccountTransaction, account_transaction


class AccountCredentialManagement(AccountManagementBase):
    async def _credential_target(
        self,
        tx: AccountTransaction,
        actor_id: str,
        principal_id: str,
        action: str,
        *,
        self_service: bool = True,
    ) -> UserPrincipal:
        actor = await self.policy.actor(tx, actor_id)
        target = principal_view(await tx.get(self.directory.users, principal_id))
        if actor_id != principal_id or not self_service:
            await self.policy.check(tx, actor, action, principal_id)
            self.policy.protect_admin(actor, target)
            await self.policy.memberships(
                tx,
                actor,
                target.roles,
                target.departments,
                assigning_roles=False,
                assigning_departments=False,
            )
        return target

    async def change_password(
        self,
        current_password: str,
        new_password: str,
        *,
        actor_id: str,
    ) -> None:
        self.validate_password(new_password)
        row = await self.directory.users.get(actor_id)
        valid = False
        if row is not None:
            with suppress(VerificationError, InvalidHashError):
                valid = await asyncio.to_thread(
                    self.directory.hasher.verify, str(row.data["password_hash"]), current_password
                )
        if row is None or not valid:
            raise OpsMeshError("UNAUTHENTICATED", "Current password is invalid")
        digest = await asyncio.to_thread(self.directory.hasher.hash, new_password)
        async with account_transaction(self.directory.database) as tx:
            await self.policy.actor(tx, actor_id)
            current = await tx.get(self.directory.users, actor_id)
            if current.data["password_hash"] != row.data["password_hash"]:
                raise OpsMeshError(
                    "CONFLICT", "Credentials changed; verify the current password again"
                )
            await tx.write(
                self.directory.users,
                actor_id,
                {
                    **current.data,
                    "password_hash": digest,
                    "session_epoch": current.data.get("session_epoch", 0) + 1,
                },
                actor_id=actor_id,
                action="accounts.password.changed",
                expected_revision=current.revision,
            )

    async def reset_password(
        self,
        principal_id: str,
        new_password: str,
        *,
        actor_id: str,
    ) -> None:
        self.validate_password(new_password)
        digest = await asyncio.to_thread(self.directory.hasher.hash, new_password)
        async with account_transaction(self.directory.database) as tx:
            await self._credential_target(
                tx, actor_id, principal_id, "accounts:credentials:manage", self_service=False
            )
            current = await tx.get(self.directory.users, principal_id)
            await tx.write(
                self.directory.users,
                principal_id,
                {
                    **current.data,
                    "password_hash": digest,
                    "session_epoch": current.data.get("session_epoch", 0) + 1,
                },
                actor_id=actor_id,
                action="accounts.password.reset",
                expected_revision=current.revision,
            )

    async def list_sessions(self, principal_id: str, *, actor_id: str) -> list[LoginSession]:
        async with account_transaction(self.directory.database) as tx:
            await self._credential_target(tx, actor_id, principal_id, "accounts:sessions:read")
            current = await tx.get(self.directory.users, principal_id)
            now = datetime.now(UTC)
            return [
                LoginSession(
                    id=row.id,
                    principal_id=principal_id,
                    created_at=row.created_at,
                    expires_at=datetime.fromisoformat(row.data["expires_at"]),
                )
                for row in await tx.list(self.directory.tokens)
                if row.data["principal_id"] == principal_id
                and row.data.get("session_epoch", 0) == current.data.get("session_epoch", 0)
                and datetime.fromisoformat(row.data["expires_at"]) > now
            ]

    async def revoke_sessions(self, principal_id: str, *, actor_id: str) -> None:
        async with account_transaction(self.directory.database) as tx:
            await self._credential_target(tx, actor_id, principal_id, "accounts:sessions:manage")
            current = await tx.get(self.directory.users, principal_id)
            await tx.write(
                self.directory.users,
                principal_id,
                {**current.data, "session_epoch": current.data.get("session_epoch", 0) + 1},
                actor_id=actor_id,
                action="accounts.sessions.revoked",
                expected_revision=current.revision,
            )

    async def revoke_session(
        self,
        principal_id: str,
        session_id: str,
        *,
        actor_id: str,
    ) -> None:
        async with account_transaction(self.directory.database) as tx:
            await self._credential_target(tx, actor_id, principal_id, "accounts:sessions:manage")
            session = await tx.get(self.directory.tokens, session_id)
            if session.data["principal_id"] != principal_id:
                raise OpsMeshError("NOT_FOUND", "Login session does not belong to this user")
            await tx.delete(
                self.directory.tokens,
                session_id,
                actor_id=actor_id,
                action="accounts.session.revoked",
                expected_revision=session.revision,
            )
