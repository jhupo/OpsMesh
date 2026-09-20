"""Installation identity and bounded host services for remote plugin processes."""

import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from opsmesh_plugin_sdk.context import PluginContext, UserContext, UserIdentity
from opsmesh_plugin_sdk.services.identity import PermissionQuery, PermissionResult
from opsmesh_plugin_sdk.services.observability import PluginLog
from opsmesh_plugin_sdk.services.storage import StoredValue, StoreWrite
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import ensure_aware_utc
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.resources import (
    ResourceAccessDenied,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.capabilities.plugins.models import (
    PluginBinding,
    PluginCredential,
    PluginInstall,
    PluginRelease,
    PluginTrustKey,
    PluginValue,
)
from backend.app.domains.capabilities.plugins.policy import require_plugin_resource
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets
from backend.app.domains.integrations.automation_contracts import AutomationConfiguration
from backend.app.domains.integrations.automation_models import Automation
from backend.app.domains.integrations.identities import ExternalIdentityService
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.service import AuditService

SERVICE_PERMISSIONS = frozenset(
    {
        "messages.receive",
        "messages.read",
        "storage.read",
        "storage.write",
        "configuration.read",
        "logs.write",
        "permissions.read",
        "identity.read",
        "resources.read",
        "knowledge.read",
        "memory.write",
        "approvals.decide",
        "attachments.write",
    }
)


@dataclass(frozen=True)
class PluginPrincipal:
    workspace_id: UUID
    install_id: UUID
    credential_id: UUID


class PluginServices:
    def __init__(self, session: Session) -> None:
        self.session = session

    def active_install(
        self, workspace_id: UUID, install_id: UUID, *, lock: bool = False
    ) -> PluginInstall:
        statement = (
            select(PluginInstall)
            .join(Workspace, Workspace.id == PluginInstall.workspace_id)
            .where(
                PluginInstall.workspace_id == workspace_id,
                PluginInstall.id == install_id,
                PluginInstall.status == "active",
                Workspace.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        item = self.session.scalar(
            statement.with_for_update(of=PluginInstall) if lock else statement
        )
        if item is None:
            raise ResourceAccessDenied()
        return item

    def active_release(self, install: PluginInstall) -> PluginRelease:
        release = self.session.scalar(
            select(PluginRelease)
            .join(
                PluginTrustKey,
                PluginTrustKey.id == PluginRelease.trust_key_id,
            )
            .where(
                PluginRelease.workspace_id == install.workspace_id,
                PluginRelease.install_id == install.id,
                PluginRelease.version == install.current_version,
                PluginRelease.status == "available",
                PluginTrustKey.workspace_id == install.workspace_id,
                PluginTrustKey.status == "active",
                PluginTrustKey.plugin_key == install.plugin_key,
            )
            .execution_options(populate_existing=True)
        )
        if release is None:
            raise ResourceAccessDenied()
        return release

    def issue(
        self,
        workspace_id: UUID,
        install_id: UUID,
        actor: AuthenticatedUser,
        permissions: list[str],
        lifetime_hours: int,
    ) -> tuple[PluginCredential, str]:
        AuthorizationService(self.session).require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        install = self.active_install(workspace_id, install_id, lock=True)
        release = self.active_release(install)
        if not permissions or not set(permissions) <= SERVICE_PERMISSIONS & set(
            release.approved_permissions
        ):
            raise ResourceAccessDenied()
        if not 1 <= lifetime_hours <= 24 * 90:
            raise ValueError("Credential lifetime must be 1 to 2160 hours")
        # Rotation revokes all previous keys atomically; raw credentials are returned once.
        for old in self.session.scalars(
            select(PluginCredential).where(
                PluginCredential.workspace_id == workspace_id,
                PluginCredential.install_id == install_id,
                PluginCredential.status == "active",
            )
        ):
            old.status = "revoked"
        token = "omp_" + secrets.token_urlsafe(32)
        record = PluginCredential(
            workspace_id=workspace_id,
            install_id=install_id,
            generation=install.generation,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            permissions=sorted(set(permissions)),
            expires_at=datetime.now(UTC) + timedelta(hours=lifetime_hours),
        )
        self.session.add(record)
        self.session.flush()
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="plugin.credential.rotated",
            target_type="plugin_install",
            target_id=install_id,
            metadata={"credential_id": str(record.id), "permissions": record.permissions},
        )
        return record, token

    def authenticate(self, workspace_id: UUID, install_id: UUID, token: str) -> PluginPrincipal:
        if not token.startswith("omp_") or len(token) > 128:
            raise ResourceAccessDenied()
        record = self.session.scalar(
            select(PluginCredential).where(
                PluginCredential.workspace_id == workspace_id,
                PluginCredential.install_id == install_id,
                PluginCredential.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            )
        )
        if record is None:
            raise ResourceAccessDenied()
        principal = PluginPrincipal(workspace_id, install_id, record.id)
        self.require(principal)
        return principal

    def require(self, principal: PluginPrincipal, permission: str | None = None) -> PluginInstall:
        install = self.active_install(principal.workspace_id, principal.install_id)
        release = self.active_release(install)
        record = self.session.scalar(
            select(PluginCredential)
            .where(
                PluginCredential.id == principal.credential_id,
                PluginCredential.workspace_id == principal.workspace_id,
                PluginCredential.install_id == principal.install_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            record is None
            or record.status != "active"
            or record.generation != install.generation
            or ensure_aware_utc(record.expires_at) <= datetime.now(UTC)
            or (
                permission is not None
                and (
                    permission not in record.permissions
                    or permission not in release.approved_permissions
                )
            )
        ):
            raise ResourceAccessDenied()
        return install

    def require_automation(
        self, principal: PluginPrincipal, automation_id: UUID, permission: str
    ) -> None:
        install = self.require(principal, permission)
        release = self.active_release(install)
        binding = self.session.scalar(
            select(PluginBinding.id).where(
                PluginBinding.workspace_id == principal.workspace_id,
                PluginBinding.install_id == principal.install_id,
                PluginBinding.release_id == release.id,
                PluginBinding.kind == "message_trigger",
                PluginBinding.resource_id == automation_id,
            )
        )
        if binding is None:
            raise ResourceAccessDenied()
        require_plugin_resource(
            self.session, principal.workspace_id, "message_trigger", automation_id
        )

    def revoke(self, workspace_id: UUID, install_id: UUID, actor: AuthenticatedUser) -> None:
        AuthorizationService(self.session).require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        self.active_install(workspace_id, install_id, lock=True)
        for record in self.session.scalars(
            select(PluginCredential).where(
                PluginCredential.workspace_id == workspace_id,
                PluginCredential.install_id == install_id,
            )
        ):
            record.status = "revoked"
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="plugin.credential.revoked",
            target_type="plugin_install",
            target_id=install_id,
            metadata={},
        )

    @staticmethod
    def validate_key(key: str) -> None:
        if key in {".", ".."} or re.fullmatch(r"[a-zA-Z0-9_.:-]{1,120}", key) is None:
            raise ValueError("Invalid plugin storage key")

    def read(self, principal: PluginPrincipal, key: str) -> StoredValue | None:
        self.require(principal, "configuration.read" if key == "configuration" else "storage.read")
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
        self.require(principal, "storage.write")
        if key == "configuration":
            raise ResourceAccessDenied()
        return self.save(principal.workspace_id, principal.install_id, key, request)

    def configure(
        self,
        workspace_id: UUID,
        install_id: UUID,
        actor: AuthenticatedUser,
        request: StoreWrite,
    ) -> StoredValue:
        AuthorizationService(self.session).require_workspace(
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
        self.validate_key(key)
        reject_embedded_secrets(request.value)
        size = len(json.dumps(request.value, ensure_ascii=True).encode())
        if size > 64_000:
            raise ValueError("Plugin value exceeds 64000 bytes")
        self.active_install(workspace_id, install_id, lock=True)
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

    def log(self, principal: PluginPrincipal, request: PluginLog) -> None:
        self.require(principal, "logs.write")
        metadata = redact_sensitive_payload(request.metadata)
        if len(json.dumps(metadata)) > 8000:
            raise ValueError("Plugin log metadata exceeds limit")
        level = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}.get(
            request.level
        )
        if level is None:
            raise ValueError("Plugin log level is invalid")
        logging.getLogger("opsmesh.plugins").log(
            level,
            request.code,
            extra={
                "plugin_workspace_id": str(principal.workspace_id),
                "plugin_install_id": str(principal.install_id),
                "plugin_credential_id": str(principal.credential_id),
                "plugin_event_id": str(request.event_id) if request.event_id else None,
                "plugin_fields": metadata,
            },
        )
        AuditService(self.session).record_system_action(
            workspace_id=principal.workspace_id,
            action="plugin.log.accepted",
            target_type="plugin_install",
            target_id=principal.install_id,
            metadata={
                "level": request.level,
                "code": request.code,
                "event_id": str(request.event_id) if request.event_id else None,
                "credential_id": str(principal.credential_id),
            },
        )

    def context(self, principal: PluginPrincipal) -> PluginContext:
        install = self.require(principal)
        release = self.active_release(install)
        credential = self.session.scalar(
            select(PluginCredential).where(
                PluginCredential.id == principal.credential_id,
                PluginCredential.workspace_id == principal.workspace_id,
                PluginCredential.install_id == principal.install_id,
            )
        )
        if credential is None:
            raise ResourceAccessDenied()
        return PluginContext(
            workspace_id=principal.workspace_id,
            install_id=principal.install_id,
            permissions=sorted(set(credential.permissions) & set(release.approved_permissions)),
        )

    def resolve_user(
        self, principal: PluginPrincipal, query: UserContext, permission: str
    ) -> AuthenticatedUser:
        self.require_automation(principal, query.automation_id, permission)
        item = self.session.scalar(
            select(Automation).where(
                Automation.workspace_id == principal.workspace_id,
                Automation.id == query.automation_id,
                Automation.status == "active",
            )
        )
        if (
            item is None
            or query.sender_id
            not in AutomationConfiguration.model_validate(item.configuration).allowed_senders
        ):
            raise ResourceAccessDenied()
        identity = ExternalIdentityService(self.session).resolve(item, query.sender_id)
        return ExecutionIdentityService(self.session).restore(principal.workspace_id, identity)

    def identity(self, principal: PluginPrincipal, query: UserContext) -> UserIdentity:
        user = self.resolve_user(principal, query, "identity.read")
        return UserIdentity(
            user_id=user.user_id,
            display_name=user.display_name,
            workspace_id=principal.workspace_id,
        )

    def permissions(self, principal: PluginPrincipal, query: PermissionQuery) -> PermissionResult:
        user = self.resolve_user(principal, query, "permissions.read")
        try:
            actions = ResourceAuthorizationService(self.session, user).effective_actions(
                principal.workspace_id,
                ResourceKind(query.resource_kind),
                query.resource_id,
            )
        except ResourceAccessDenied:
            actions = []
        return PermissionResult(actions=[action.value for action in actions])

    def values(self, principal: PluginPrincipal, prefix: str, offset: int) -> list[StoredValue]:
        self.require(principal, "storage.read")
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
        self.require(principal, "storage.write")
        self.validate_key(key)
        if key == "configuration":
            raise ResourceAccessDenied()
        self.active_install(principal.workspace_id, principal.install_id, lock=True)
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
