"""Transactions for signed remote plugin installation and release lifecycle."""

import base64
from uuid import UUID

from opsmesh_plugin_sdk.packages import verify_package
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.errors import flush_or_raise_conflict
from backend.app.core.errors import ConflictError, NotFoundError, PolicyDeniedError
from backend.app.core.utils import payload_hash
from backend.app.domains.access.models import User
from backend.app.domains.access.permissions import WorkspaceAction, role_allows
from backend.app.domains.capabilities.marketplace.models import WorkspaceMarketplaceInstall
from backend.app.domains.capabilities.plugins.contracts import (
    PluginAction,
    PluginInstallRequest,
    TrustKeyCreate,
)
from backend.app.domains.capabilities.plugins.dependencies import binding_dependents
from backend.app.domains.capabilities.plugins.models import (
    PluginBinding,
    PluginInstall,
    PluginRelease,
    PluginTrustKey,
)
from backend.app.domains.capabilities.plugins.policy import resource_configuration
from backend.app.domains.capabilities.resources.schema import (
    reject_embedded_secrets,
    validate_json_schema,
    validate_parameters,
)
from backend.app.domains.workspace.tenants.models import Workspace, WorkspaceMember
from backend.app.observability.audit.service import AuditService


class PluginService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def trust_key(
        self, workspace_id: UUID, user_id: UUID, request: TrustKeyCreate
    ) -> PluginTrustKey:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        self.require_admin(workspace_id, user_id)
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(request.public_key, validate=True))
        except ValueError as exc:
            raise ValueError("Invalid Ed25519 public key") from exc
        key = PluginTrustKey(workspace_id=workspace_id, **request.model_dump())
        self.session.add(key)
        flush_or_raise_conflict(self.session, "Publisher key ID already registered")
        self._audit(
            workspace_id, user_id, "plugin.key_trusted", key.id, {"plugin_key": key.plugin_key}
        )
        self.session.commit()
        return key

    def revoke_key(self, workspace_id: UUID, user_id: UUID, key_id: UUID) -> PluginTrustKey:
        self.require_admin(workspace_id, user_id)
        key = self.session.scalar(
            select(PluginTrustKey)
            .where(
                PluginTrustKey.workspace_id == workspace_id,
                PluginTrustKey.id == key_id,
            )
            .with_for_update()
        )
        if key is None:
            raise NotFoundError("Publisher key not found")
        key.status = "revoked"
        self._audit(workspace_id, user_id, "plugin.key_revoked", key.id, {})
        self.session.commit()
        return key

    def install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        request: PluginInstallRequest,
        *,
        commit: bool = True,
    ) -> PluginInstall:
        self.require_admin(workspace_id, user_id)
        package = request.package
        manifest = package.manifest
        reject_embedded_secrets(manifest.model_dump(mode="json"))
        key = self.session.scalar(
            select(PluginTrustKey)
            .where(
                PluginTrustKey.workspace_id == workspace_id,
                PluginTrustKey.key_id == package.publisher_key_id,
                PluginTrustKey.plugin_key == manifest.key,
                PluginTrustKey.status == "active",
            )
            .with_for_update()
        )
        if key is None:
            raise PolicyDeniedError("Publisher key is not trusted for this plugin")
        verify_package(package, base64.b64decode(key.public_key, validate=True))
        permissions = {
            p for capability in manifest.capabilities for p in capability.required_permissions
        }
        if set(request.approved_permissions) != permissions:
            raise PolicyDeniedError("Explicit approval of every declared permission is required")
        if set(request.bindings) != {capability.key for capability in manifest.capabilities}:
            raise ValueError("Every plugin capability must have exactly one resource binding")
        install = self.session.scalar(
            select(PluginInstall)
            .where(
                PluginInstall.workspace_id == workspace_id,
                PluginInstall.plugin_key == manifest.key,
            )
            .with_for_update()
        )
        if install is None:
            if request.expected_generation is not None:
                raise ConflictError("Plugin is not installed")
            install = PluginInstall(
                workspace_id=workspace_id,
                plugin_key=manifest.key,
                current_version=manifest.version,
            )
            self.session.add(install)
            self.session.flush()
        else:
            self._check_generation(install, request.expected_generation)
            if install.status == "uninstalled":
                raise ConflictError("Uninstalled plugin identity cannot be reused")
            install.generation += 1
        release = self.session.scalar(
            select(PluginRelease).where(
                PluginRelease.workspace_id == workspace_id,
                PluginRelease.install_id == install.id,
                PluginRelease.version == manifest.version,
            )
        )
        if release is not None:
            raise ConflictError(
                "Plugin version is immutable; use switch_version for an installed release"
            )
        release = PluginRelease(
            workspace_id=workspace_id,
            install_id=install.id,
            trust_key_id=key.id,
            version=manifest.version,
            package=package.model_dump(mode="json"),
            checksum=payload_hash(package.model_dump(mode="json")),
            approved_permissions=sorted(permissions),
        )
        self.session.add(release)
        self.session.flush()
        for capability in manifest.capabilities:
            binding = request.bindings[capability.key]
            config = resource_configuration(
                self.session, workspace_id, capability.kind, binding.resource_id
            )
            reject_embedded_secrets(config)
            validate_json_schema(capability.configuration_schema)
            validate_parameters(config, capability.configuration_schema, label="plugin binding")
            self.session.add(
                PluginBinding(
                    workspace_id=workspace_id,
                    install_id=install.id,
                    release_id=release.id,
                    capability_key=capability.key,
                    kind=capability.kind,
                    resource_id=binding.resource_id,
                    configuration=config,
                )
            )
        flush_or_raise_conflict(
            self.session, "Capability resource already belongs to a plugin release"
        )
        install.current_version = manifest.version
        self._audit(
            workspace_id,
            user_id,
            "plugin.release_installed",
            install.id,
            {
                "version": manifest.version,
                "generation": install.generation,
                "checksum": release.checksum,
            },
        )
        if commit:
            self.session.commit()
        return install

    def require(self, workspace_id: UUID, install_id: UUID, *, lock: bool = False) -> PluginInstall:
        query = (
            select(PluginInstall)
            .where(
                PluginInstall.workspace_id == workspace_id,
                PluginInstall.id == install_id,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        item = self.session.scalar(query)
        if item is None:
            raise NotFoundError("Plugin installation not found")
        return item

    def action(
        self, workspace_id: UUID, user_id: UUID, install_id: UUID, request: PluginAction
    ) -> PluginInstall:
        self.require_admin(workspace_id, user_id)
        install = self.require(workspace_id, install_id, lock=True)
        self._check_generation(install, request.expected_generation)
        if install.status == "uninstalled":
            raise ConflictError("Plugin was uninstalled")
        release = None
        if request.action in {"enable", "switch_version", "retire_version"}:
            version = install.current_version if request.action == "enable" else request.version
            release = self.session.scalar(
                select(PluginRelease)
                .where(
                    PluginRelease.workspace_id == workspace_id,
                    PluginRelease.install_id == install.id,
                    PluginRelease.version == version,
                    PluginRelease.status == "available",
                )
                .with_for_update()
            )
            if release is None:
                raise NotFoundError("Available plugin version not found")
        if (
            request.action not in {"switch_version", "retire_version"}
            and request.version is not None
        ):
            raise ValueError("Version is only valid for switch_version and retire_version")
        if request.action in {"uninstall", "retire_version"}:
            if release is not None and release.version == install.current_version:
                raise ConflictError("Cannot retire the current plugin version")
            dependencies = self.dependencies(
                workspace_id, install.id, release.id if release else None
            )
            if dependencies:
                raise ConflictError(
                    "Plugin still has dependents", details={"dependents": dependencies}
                )
        if request.action in {"enable", "switch_version"}:
            assert release is not None
            key = self.session.scalar(
                select(PluginTrustKey).where(
                    PluginTrustKey.workspace_id == workspace_id,
                    PluginTrustKey.id == release.trust_key_id,
                    PluginTrustKey.status == "active",
                )
            )
            if key is None:
                raise PolicyDeniedError("Plugin version publisher key is revoked")
            for binding in self.session.scalars(
                select(PluginBinding).where(
                    PluginBinding.workspace_id == workspace_id,
                    PluginBinding.release_id == release.id,
                )
            ):
                if (
                    resource_configuration(
                        self.session, workspace_id, binding.kind, binding.resource_id
                    )
                    != binding.configuration
                ):
                    raise ConflictError("Plugin binding configuration has changed")
            install.current_version = release.version
            if request.action == "enable":
                install.status = "active"
        elif request.action == "retire_version":
            assert release is not None
            release.status = "retired"
        else:
            install.status = {
                "enable": "active",
                "disable": "disabled",
                "uninstall": "uninstalled",
            }[request.action]
        install.generation += 1
        for market_install in self.session.scalars(
            select(WorkspaceMarketplaceInstall).where(
                WorkspaceMarketplaceInstall.workspace_id == workspace_id,
                WorkspaceMarketplaceInstall.listing_type == "plugin",
                WorkspaceMarketplaceInstall.installed_resource_id == install.id,
            )
        ):
            market_install.status = install.status
        self._audit(
            workspace_id,
            user_id,
            f"plugin.{request.action}",
            install.id,
            {
                "generation": install.generation,
                "version": request.version,
            },
        )
        self.session.commit()
        return install

    def dependencies(
        self, workspace_id: UUID, install_id: UUID, release_id: UUID | None = None
    ) -> list[str]:
        self.require(workspace_id, install_id)
        query = select(PluginBinding).where(
            PluginBinding.workspace_id == workspace_id,
            PluginBinding.install_id == install_id,
        )
        if release_id is not None:
            query = query.where(PluginBinding.release_id == release_id)
        return binding_dependents(self.session, workspace_id, list(self.session.scalars(query)))

    @staticmethod
    def _check_generation(install: PluginInstall, expected: int | None) -> None:
        if expected != install.generation:
            raise ConflictError("Plugin installation changed; reload before modifying")

    def require_admin(self, workspace_id: UUID, user_id: UUID) -> None:
        # Use the audit writer's workspace-first lock order for every plugin mutation.
        self.session.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
        member = self.session.scalar(
            select(WorkspaceMember)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                User.status == "active",
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        if member is None or not role_allows(member.role, WorkspaceAction.ADMIN):
            raise PolicyDeniedError("Plugin trust and lifecycle require a workspace administrator")

    def _audit(
        self,
        workspace_id: UUID,
        user_id: UUID,
        action: str,
        target_id: UUID,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="plugin",
            target_id=target_id,
            metadata=metadata,
        )
