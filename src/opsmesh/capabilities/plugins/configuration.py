"""Tenant-scoped bounded plugin state and audited installation configuration."""

import json
import re
from uuid import UUID

from opsmesh_plugin_sdk.services.storage import StoredValue, StoreWrite
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.capabilities.plugins.models import (
    PluginValue,
)
from opsmesh.capabilities.references.schema import reject_embedded_secrets
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAccessDeniedError,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.shared.errors import DomainError


class PluginConfigurationService:
    """Store installation configuration and bounded connector KV under an installation lock."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def validate_key(key: str) -> None:
        if key in {".", ".."} or re.fullmatch(r"[a-zA-Z0-9_.:-]{1,120}", key) is None:
            raise ValueError("Invalid plugin storage key")

    def read(self, principal: PluginPrincipal, key: str) -> StoredValue | None:
        PluginAuthenticationService(self.session).require(
            principal, "configuration.read" if key == "configuration" else "storage.read"
        )
        self.validate_key(key)
        row = self.session.scalar(
            select(PluginValue).where(
                PluginValue.workspace_id == principal.workspace_id,
                PluginValue.install_id == principal.install_id,
                PluginValue.key == key,
            )
        )
        return StoredValue(key=row.key, revision=row.revision, value=row.value) if row else None

    def write(self, principal: PluginPrincipal, key: str, request: StoreWrite) -> StoredValue:
        PluginAuthenticationService(self.session).require(principal, "storage.write")
        if key == "configuration":
            raise ResourceAccessDeniedError()
        return self.save(principal.workspace_id, principal.install_id, key, request)

    def configure(
        self,
        workspace_id: UUID,
        install_id: UUID,
        actor: AuthenticatedUser,
        request: StoreWrite,
    ) -> StoredValue:
        """Authorize and audit a configuration change in the caller's transaction."""
        WorkspaceAuthorizationService(self.session).require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        value = self.save(workspace_id, install_id, "configuration", request)
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="plugin.configuration.updated",
            target_type="plugin_install",
            target_id=install_id,
            metadata={"revision": value.revision},
        )
        return value

    def save(
        self, workspace_id: UUID, install_id: UUID, key: str, request: StoreWrite
    ) -> StoredValue:
        """Validate and flush an optimistic KV update; the caller owns commit or rollback."""
        self.validate_key(key)
        reject_embedded_secrets(request.value)
        size = len(json.dumps(request.value, ensure_ascii=True).encode())
        if size > 64_000:
            raise ValueError("Plugin value exceeds 64000 bytes")
        PluginAuthenticationService(self.session).active_install(
            workspace_id, install_id, lock=True
        )
        rows = list(
            self.session.scalars(
                select(PluginValue)
                .where(
                    PluginValue.workspace_id == workspace_id,
                    PluginValue.install_id == install_id,
                )
                .execution_options(populate_existing=True)
            )
        )
        row = next((item for item in rows if item.key == key), None)
        if request.expected_revision != (row.revision if row else 0):
            raise DomainError(
                "Plugin state revision conflict", code="plugin_state_conflict", status_code=409
            )
        if (row is None and len(rows) >= 512) or sum(
            len(json.dumps(item.value, ensure_ascii=True).encode())
            for item in rows
            if item.key != key
        ) + size > 2_000_000:
            raise DomainError(
                "Plugin storage quota exceeded", code="plugin_storage_quota", status_code=413
            )
        if row is None:
            row = PluginValue(
                workspace_id=workspace_id,
                install_id=install_id,
                key=key,
                revision=1,
                value=request.value,
            )
            self.session.add(row)
        else:
            row.value = request.value
            row.revision += 1
        self.session.flush()
        return StoredValue(key=key, revision=row.revision, value=row.value)

    def values(self, principal: PluginPrincipal, prefix: str, offset: int) -> list[StoredValue]:
        PluginAuthenticationService(self.session).require(principal, "storage.read")
        rows = self.session.scalars(
            select(PluginValue)
            .where(
                PluginValue.workspace_id == principal.workspace_id,
                PluginValue.install_id == principal.install_id,
                PluginValue.key != "configuration",
                PluginValue.key.startswith(prefix, autoescape=True),
            )
            .order_by(PluginValue.key)
            .offset(offset)
            .limit(100)
        )
        return [StoredValue(key=row.key, revision=row.revision, value=row.value) for row in rows]

    def delete(self, principal: PluginPrincipal, key: str, expected_revision: int) -> None:
        PluginAuthenticationService(self.session).require(principal, "storage.write")
        self.validate_key(key)
        if key == "configuration":
            raise ResourceAccessDeniedError()
        PluginAuthenticationService(self.session).active_install(
            principal.workspace_id, principal.install_id, lock=True
        )
        row = self.session.scalar(
            select(PluginValue)
            .where(
                PluginValue.workspace_id == principal.workspace_id,
                PluginValue.install_id == principal.install_id,
                PluginValue.key == key,
            )
            .execution_options(populate_existing=True)
        )
        if row is None or row.revision != expected_revision:
            raise DomainError(
                "Plugin state revision conflict", code="plugin_state_conflict", status_code=409
            )
        self.session.delete(row)
