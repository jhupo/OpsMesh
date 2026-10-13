"""Transactions for signed remote plugin installation and release lifecycle."""

import base64
from uuid import UUID

from opsmesh_plugin_sdk.packaging.packages import SignedPluginPackage, verify_package
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.marketplace.models import WorkspaceMarketplaceInstall
from opsmesh.capabilities.plugins.bindings import (
    bind_resource,
    reauthorize_binding,
    validate_existing_bindings,
)
from opsmesh.capabilities.plugins.contracts import (
    PluginActionRequest,
    PluginBindingReauthorizeRequest,
    PluginBindingRequest,
    PluginInstallRequest,
    PluginTrustKeyCreateRequest,
)
from opsmesh.capabilities.plugins.dependencies import binding_dependents
from opsmesh.capabilities.plugins.models import (
    PluginBinding,
    PluginCredential,
    PluginInstallation,
    PluginRelease,
    PluginTrustKey,
)
from opsmesh.capabilities.references.schema import reject_embedded_secrets, validate_json_schema
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.permissions import WorkspaceAction, role_allows
from opsmesh.identity.users.models import User
from opsmesh.shared.db.errors import flush_or_raise_conflict
from opsmesh.shared.errors import ConflictError, NotFoundError, PolicyDeniedError
from opsmesh.shared.utils import payload_hash
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember


class PluginLifecycleService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def trust_key(
        self, workspace_id: UUID, user_id: UUID, request: PluginTrustKeyCreateRequest
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
        if key.status != "revoked":
            key.status = "revoked"
            self._revoke_current_key_installations(workspace_id, key.id, platform_block=False)
            self._audit(workspace_id, user_id, "plugin.key_revoked", key.id, {})
        self.session.commit()
        return key

    def platform_revoke_key(
        self, workspace_id: UUID, key_id: UUID, *, reason: str
    ) -> PluginTrustKey:
        self._lock_workspace(workspace_id)
        key = self.session.scalar(
            select(PluginTrustKey)
            .where(PluginTrustKey.workspace_id == workspace_id, PluginTrustKey.id == key_id)
            .with_for_update()
        )
        if key is None:
            raise NotFoundError("Publisher key not found")
        key.status = "revoked"
        affected_installs = self._revoke_current_key_installations(
            workspace_id, key.id, platform_block=True
        )
        AuditService(self.session).record_system_action(
            workspace_id=workspace_id,
            action="platform.plugin.publisher_key_revoked",
            target_type="plugin_trust_key",
            target_id=key.id,
            metadata={
                "plugin_key": key.plugin_key,
                "key_id": key.key_id,
                "affected_install_ids": [str(install_id) for install_id in affected_installs],
                "reason": reason,
            },
            actor_id="platform_admin",
        )
        self.session.commit()
        self.session.refresh(key)
        return key

    def platform_disable(
        self, workspace_id: UUID, install_id: UUID, *, reason: str
    ) -> PluginInstallation:
        self._lock_workspace(workspace_id)
        install = self.require(workspace_id, install_id, lock=True)
        if install.status == "uninstalled":
            raise ConflictError("Uninstalled plugin cannot be platform disabled")
        before = install.status
        if not install.platform_blocked:
            install.platform_blocked = True
            install.status = "disabled"
            install.generation += 1
        self._stop_install(workspace_id, install.id)
        self._sync_marketplace_status(workspace_id, install)
        AuditService(self.session).record_system_action(
            workspace_id=workspace_id,
            action="platform.plugin.disabled",
            target_type="plugin_install",
            target_id=install.id,
            metadata={"before": before, "after": install.status, "reason": reason},
            actor_id="platform_admin",
        )
        self.session.commit()
        self.session.refresh(install)
        return install

    def platform_release(
        self, workspace_id: UUID, install_id: UUID, *, reason: str
    ) -> PluginInstallation:
        self._lock_workspace(workspace_id)
        install = self.require(workspace_id, install_id, lock=True)
        if install.platform_blocked:
            install.platform_blocked = False
            AuditService(self.session).record_system_action(
                workspace_id=workspace_id,
                action="platform.plugin.released",
                target_type="plugin_install",
                target_id=install.id,
                metadata={"status": install.status, "reason": reason},
                actor_id="platform_admin",
            )
        self.session.commit()
        self.session.refresh(install)
        return install

    def _revoke_current_key_installations(
        self, workspace_id: UUID, key_id: UUID, *, platform_block: bool
    ) -> list[UUID]:
        affected: list[UUID] = []
        releases = self.session.scalars(
            select(PluginRelease).where(
                PluginRelease.workspace_id == workspace_id,
                PluginRelease.trust_key_id == key_id,
            )
        ).all()
        for release in releases:
            installations = self.session.scalars(
                select(PluginInstallation).where(
                    PluginInstallation.workspace_id == workspace_id,
                    PluginInstallation.current_release_id == release.id,
                )
            )
            for install in installations:
                affected.append(install.id)
                if platform_block and not install.platform_blocked:
                    install.platform_blocked = True
                    install.status = "disabled"
                    install.generation += 1
                    self._sync_marketplace_status(workspace_id, install)
                self._stop_install(workspace_id, install.id)
        return affected

    def _stop_install(self, workspace_id: UUID, install_id: UUID) -> None:
        for credential in self.session.scalars(
            select(PluginCredential).where(
                PluginCredential.workspace_id == workspace_id,
                PluginCredential.install_id == install_id,
                PluginCredential.status == "active",
            )
        ):
            credential.status = "revoked"

    def _sync_marketplace_status(self, workspace_id: UUID, install: PluginInstallation) -> None:
        for market_install in self.session.scalars(
            select(WorkspaceMarketplaceInstall).where(
                WorkspaceMarketplaceInstall.workspace_id == workspace_id,
                WorkspaceMarketplaceInstall.listing_type == "plugin",
                WorkspaceMarketplaceInstall.installed_resource_id == install.id,
            )
        ):
            market_install.status = install.status

    def install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        request: PluginInstallRequest,
        *,
        commit: bool = True,
    ) -> PluginInstallation:
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
        for capability in manifest.capabilities:
            validate_json_schema(capability.configuration_schema)
        permissions = {
            p for capability in manifest.capabilities for p in capability.required_permissions
        }
        if set(request.approved_permissions) != permissions:
            raise PolicyDeniedError("Explicit approval of every declared permission is required")
        install = None
        if request.installation_id is not None:
            install = self.require(workspace_id, request.installation_id, lock=True)
            self._check_generation(install, request.expected_generation)
            if install.plugin_key != manifest.key or install.instance_name != request.instance_name:
                raise ConflictError("Upgrade must retain installation identity and instance name")
            if install.platform_blocked:
                raise PolicyDeniedError("Plugin installation is blocked by the platform")
            if install.status == "uninstalled":
                raise ConflictError("Uninstalled plugin identity cannot be reused")
        elif request.expected_generation is not None:
            raise ValueError("Upgrade requires an explicit installation_id")
        release = self.session.scalar(
            select(PluginRelease).where(
                PluginRelease.workspace_id == workspace_id,
                PluginRelease.plugin_key == manifest.key,
                PluginRelease.version == manifest.version,
            )
        )
        package_data = package.model_dump(mode="json")
        checksum = payload_hash(package_data)
        if release is None:
            release = PluginRelease(
                workspace_id=workspace_id,
                plugin_key=manifest.key,
                trust_key_id=key.id,
                version=manifest.version,
                package=package_data,
                checksum=checksum,
            )
            self.session.add(release)
            self.session.flush()
        elif release.checksum != checksum or release.trust_key_id != key.id:
            raise ConflictError("Plugin release identity is immutable")
        if release.status != "available":
            raise PolicyDeniedError("Plugin release is retired")
        if install is None:
            install = PluginInstallation(
                workspace_id=workspace_id,
                plugin_key=manifest.key,
                instance_name=request.instance_name,
                current_version=manifest.version,
                current_release_id=release.id,
                approved_permissions=sorted(permissions),
            )
            self.session.add(install)
            flush_or_raise_conflict(
                self.session, "Plugin installation instance name already exists"
            )
        else:
            install.approved_permissions = sorted(permissions)
            validate_existing_bindings(self.session, install, release)
            install.generation += 1
        install.current_release_id = release.id
        install.current_version = manifest.version
        for binding in request.bindings:
            bind_resource(self.session, install, binding)
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

    def require(
        self, workspace_id: UUID, install_id: UUID, *, lock: bool = False
    ) -> PluginInstallation:
        query = (
            select(PluginInstallation)
            .where(
                PluginInstallation.workspace_id == workspace_id,
                PluginInstallation.id == install_id,
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
        self, workspace_id: UUID, user_id: UUID, install_id: UUID, request: PluginActionRequest
    ) -> PluginInstallation:
        self.require_admin(workspace_id, user_id)
        install = self.require(workspace_id, install_id, lock=True)
        self._check_generation(install, request.expected_generation)
        if install.platform_blocked and request.action in {"enable", "switch_version"}:
            raise PolicyDeniedError("Plugin installation is blocked by the platform")
        if install.status == "uninstalled":
            raise ConflictError("Plugin was uninstalled")
        release = None
        if request.action in {"enable", "switch_version", "retire_version"}:
            version = install.current_version if request.action == "enable" else request.version
            release = self.session.scalar(
                select(PluginRelease)
                .where(
                    PluginRelease.workspace_id == workspace_id,
                    PluginRelease.plugin_key == install.plugin_key,
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
            if (
                release is not None
                and self.session.scalar(
                    select(PluginInstallation.id).where(
                        PluginInstallation.workspace_id == workspace_id,
                        PluginInstallation.current_release_id == release.id,
                        PluginInstallation.status != "uninstalled",
                    )
                )
                is not None
            ):
                raise ConflictError("Cannot retire a release used by an installation")
            dependencies = self.dependencies(workspace_id, install.id) if release is None else []
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
            package = release.package
            manifest = SignedPluginPackage.model_validate(package).manifest
            permissions = {p for cap in manifest.capabilities for p in cap.required_permissions}
            approved = request.approved_permissions
            if approved is not None:
                if set(approved) != permissions:
                    raise PolicyDeniedError(
                        "Explicit approval of every declared permission is required"
                    )
                install.approved_permissions = sorted(permissions)
            elif not permissions.issubset(install.approved_permissions):
                raise PolicyDeniedError("Release requires explicit approval of added permissions")
            install.approved_permissions = sorted(permissions)
            validate_existing_bindings(self.session, install, release)
            install.current_release_id = release.id
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
        if request.action in {"disable", "uninstall"}:
            self._stop_install(workspace_id, install.id)
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

    def dependencies(self, workspace_id: UUID, install_id: UUID) -> list[str]:
        self.require(workspace_id, install_id)
        query = select(PluginBinding).where(
            PluginBinding.workspace_id == workspace_id,
            PluginBinding.install_id == install_id,
        )
        return binding_dependents(self.session, workspace_id, list(self.session.scalars(query)))

    def bind(
        self, workspace_id: UUID, user_id: UUID, install_id: UUID, request: PluginBindingRequest
    ) -> PluginBinding:
        self.require_admin(workspace_id, user_id)
        installation = self.require(workspace_id, install_id, lock=True)
        self._check_generation(installation, request.expected_generation)
        if installation.platform_blocked or installation.status != "active":
            raise PolicyDeniedError("Plugin installation is unavailable")
        binding = bind_resource(self.session, installation, request)
        installation.generation += 1
        self._audit(
            workspace_id,
            user_id,
            "plugin.resource_bound",
            binding.id,
            {"installation_id": str(install_id)},
        )
        self.session.commit()
        return binding

    def reauthorize(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        binding_id: UUID,
        request: PluginBindingReauthorizeRequest,
    ) -> PluginBinding:
        self.require_admin(workspace_id, user_id)
        installation = self.require(workspace_id, install_id, lock=True)
        self._check_generation(installation, request.expected_generation)
        if installation.platform_blocked or installation.status == "uninstalled":
            raise PolicyDeniedError("Plugin installation is unavailable")
        binding = reauthorize_binding(
            self.session, installation, binding_id, request.expected_configuration_revision
        )
        installation.generation += 1
        self._audit(
            workspace_id,
            user_id,
            "plugin.binding_reauthorized",
            binding.id,
            {"configuration_revision": binding.configuration_revision},
        )
        self.session.commit()
        return binding

    def unbind(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        binding_id: UUID,
        expected_generation: int,
    ) -> None:
        """Release resource ownership after rejecting live or retained product dependencies."""
        self.require_admin(workspace_id, user_id)
        installation = self.require(workspace_id, install_id, lock=True)
        self._check_generation(installation, expected_generation)
        binding = self.session.scalar(
            select(PluginBinding)
            .where(
                PluginBinding.workspace_id == workspace_id,
                PluginBinding.install_id == install_id,
                PluginBinding.id == binding_id,
            )
            .with_for_update()
        )
        if binding is None:
            raise NotFoundError("Plugin binding not found")
        dependencies = binding_dependents(self.session, workspace_id, [binding])
        if dependencies:
            raise ConflictError(
                "Plugin binding still has dependents", details={"dependents": dependencies}
            )
        installation.generation += 1
        self._audit(
            workspace_id,
            user_id,
            "plugin.resource_unbound",
            binding.id,
            {"installation_id": str(install_id), "resource_id": str(binding.resource_id)},
        )
        self.session.delete(binding)
        self.session.commit()

    @staticmethod
    def _check_generation(install: PluginInstallation, expected: int | None) -> None:
        if expected != install.generation:
            raise ConflictError("Plugin installation changed; reload before modifying")

    def require_admin(self, workspace_id: UUID, user_id: UUID) -> None:
        # Use the audit writer's workspace-first lock order for every plugin mutation.
        self._lock_workspace(workspace_id)
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

    def _lock_workspace(self, workspace_id: UUID) -> None:
        self.session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
        )

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
