"""Installation credentials and live remote-plugin authorization."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from opsmesh_plugin_sdk.context import PluginContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.models import (
    PluginCredential,
    PluginInstallation,
    PluginRelease,
    PluginTrustKey,
)
from opsmesh.capabilities.plugins.policy import require_plugin_resource
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAccessDeniedError,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.messaging.channels.models import ChannelConnection
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.models import Automation
from opsmesh.shared.utils import ensure_aware_utc
from opsmesh.workspaces.management.models import Workspace

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
    """Authenticated service identity scoped to one installation."""

    workspace_id: UUID
    install_id: UUID
    credential_id: UUID


class PluginAuthenticationService:
    """Authenticate installation tokens and enforce current tenant grants."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def active_install(
        self, workspace_id: UUID, install_id: UUID, *, lock: bool = False
    ) -> PluginInstallation:
        statement = (
            select(PluginInstallation)
            .join(Workspace, Workspace.id == PluginInstallation.workspace_id)
            .where(
                PluginInstallation.workspace_id == workspace_id,
                PluginInstallation.id == install_id,
                PluginInstallation.status == "active",
                PluginInstallation.platform_blocked.is_(False),
                Workspace.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        item = self.session.scalar(
            statement.with_for_update(of=PluginInstallation) if lock else statement
        )
        if item is None:
            raise ResourceAccessDeniedError()
        return item

    def active_release(self, install: PluginInstallation) -> PluginRelease:
        release = self.session.scalar(
            select(PluginRelease)
            .join(
                PluginTrustKey,
                PluginTrustKey.id == PluginRelease.trust_key_id,
            )
            .where(
                PluginRelease.workspace_id == install.workspace_id,
                PluginRelease.id == install.current_release_id,
                PluginRelease.plugin_key == install.plugin_key,
                PluginRelease.version == install.current_version,
                PluginRelease.status == "available",
                PluginTrustKey.workspace_id == install.workspace_id,
                PluginTrustKey.status == "active",
                PluginTrustKey.plugin_key == install.plugin_key,
            )
            .execution_options(populate_existing=True)
        )
        if release is None:
            raise ResourceAccessDeniedError()
        return release

    def rotate_credential(
        self,
        workspace_id: UUID,
        install_id: UUID,
        actor: AuthenticatedUser,
        permissions: list[str],
        lifetime_hours: int,
    ) -> tuple[PluginCredential, str]:
        """Rotate service credentials in the caller's transaction; return the token once."""
        WorkspaceAuthorizationService(self.session).require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        install = self.active_install(workspace_id, install_id, lock=True)
        self.active_release(install)
        if not permissions or not set(permissions) <= SERVICE_PERMISSIONS & set(
            install.approved_permissions
        ):
            raise ResourceAccessDeniedError()
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
            raise ResourceAccessDeniedError()
        record = self.session.scalar(
            select(PluginCredential).where(
                PluginCredential.workspace_id == workspace_id,
                PluginCredential.install_id == install_id,
                PluginCredential.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            )
        )
        if record is None:
            raise ResourceAccessDeniedError()
        principal = PluginPrincipal(workspace_id, install_id, record.id)
        self.require(principal)
        return principal

    def require(
        self, principal: PluginPrincipal, permission: str | None = None
    ) -> PluginInstallation:
        """Recheck live grants without invalidating credentials on a safe release upgrade."""
        install = self.active_install(principal.workspace_id, principal.install_id)
        self.active_release(install)
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
            or ensure_aware_utc(record.expires_at) <= datetime.now(UTC)
            or (
                permission is not None
                and (
                    permission not in record.permissions
                    or permission not in install.approved_permissions
                )
            )
        ):
            raise ResourceAccessDeniedError()
        return install

    def require_automation(
        self, principal: PluginPrincipal, automation_id: UUID, permission: str
    ) -> None:
        self.require(principal, permission)
        automation = self.session.scalar(
            select(Automation)
            .where(
                Automation.workspace_id == principal.workspace_id,
                Automation.id == automation_id,
                Automation.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        if automation is None:
            raise ResourceAccessDeniedError()
        configuration = AutomationConfiguration.model_validate(automation.configuration)
        connection = self.session.scalar(
            select(ChannelConnection)
            .where(
                ChannelConnection.workspace_id == principal.workspace_id,
                ChannelConnection.id == configuration.channel_connection_id,
                ChannelConnection.install_id == principal.install_id,
                ChannelConnection.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        if connection is None:
            raise ResourceAccessDeniedError()
        require_plugin_resource(self.session, principal.workspace_id, "channel", connection.id)

    def revoke(self, workspace_id: UUID, install_id: UUID, actor: AuthenticatedUser) -> None:
        WorkspaceAuthorizationService(self.session).require_workspace(
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

    def context(self, principal: PluginPrincipal) -> PluginContext:
        install = self.require(principal)
        credential = self.session.scalar(
            select(PluginCredential).where(
                PluginCredential.id == principal.credential_id,
                PluginCredential.workspace_id == principal.workspace_id,
                PluginCredential.install_id == principal.install_id,
            )
        )
        if credential is None:
            raise ResourceAccessDeniedError()
        return PluginContext(
            workspace_id=principal.workspace_id,
            install_id=principal.install_id,
            permissions=sorted(set(credential.permissions) & set(install.approved_permissions)),
        )
