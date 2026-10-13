"""Stable installation resource bindings and explicit configuration authorization."""

from uuid import UUID

from opsmesh_plugin_sdk.packaging.manifest import CapabilityDeclaration
from opsmesh_plugin_sdk.packaging.packages import SignedPluginPackage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.contracts import PluginCapabilityBinding
from opsmesh.capabilities.plugins.models import PluginBinding, PluginInstallation, PluginRelease
from opsmesh.capabilities.plugins.policy import (
    resource_configuration,
    resource_configuration_revision,
)
from opsmesh.capabilities.references.schema import (
    reject_embedded_secrets,
    validate_json_schema,
    validate_parameters,
)
from opsmesh.shared.db.errors import flush_or_raise_conflict
from opsmesh.shared.errors import ConflictError, NotFoundError, PolicyDeniedError


def release_capabilities(release: PluginRelease) -> dict[str, CapabilityDeclaration]:
    package = SignedPluginPackage.model_validate(release.package)
    return {capability.key: capability for capability in package.manifest.capabilities}


def validate_binding(
    session: Session,
    installation: PluginInstallation,
    capability: CapabilityDeclaration,
    resource_id: UUID,
) -> tuple[dict[str, object], int]:
    """Validate live resource configuration without changing or committing authorization."""
    if not set(capability.required_permissions).issubset(installation.approved_permissions):
        raise PolicyDeniedError("Plugin capability permissions have not been approved")
    configuration = resource_configuration(
        session, installation.workspace_id, capability.kind, resource_id
    )
    if str(capability.kind) == "channel":
        from opsmesh.messaging.channels.models import ChannelConnection

        connection = session.scalar(
            select(ChannelConnection).where(
                ChannelConnection.workspace_id == installation.workspace_id,
                ChannelConnection.id == resource_id,
                ChannelConnection.install_id == installation.id,
                ChannelConnection.capability_key == capability.key,
            )
        )
        if connection is None:
            raise PolicyDeniedError(
                "Channel connection belongs to another installation or capability"
            )
    reject_embedded_secrets(configuration)
    validate_json_schema(capability.configuration_schema)
    validate_parameters(configuration, capability.configuration_schema, label="plugin binding")
    revision = resource_configuration_revision(
        session, installation.workspace_id, capability.kind, resource_id
    )
    return configuration, revision


def installation_bindings(
    session: Session, installation: PluginInstallation
) -> list[PluginBinding]:
    return list(
        session.scalars(
            select(PluginBinding)
            .where(
                PluginBinding.workspace_id == installation.workspace_id,
                PluginBinding.install_id == installation.id,
            )
            .order_by(PluginBinding.id)
        )
    )


def validate_existing_bindings(
    session: Session, installation: PluginInstallation, release: PluginRelease
) -> None:
    """Validate an upgrade while preserving binding IDs and authorization revisions."""
    capabilities = release_capabilities(release)
    for binding in installation_bindings(session, installation):
        capability = capabilities.get(binding.capability_key)
        if capability is None or capability.kind != binding.kind:
            raise ConflictError("New release removes or changes a bound capability")
        configuration, revision = validate_binding(
            session, installation, capability, binding.resource_id
        )
        if revision != binding.configuration_revision:
            raise ConflictError("Reauthorize changed resource configuration before upgrading")
        binding.configuration = configuration


def bind_resource(
    session: Session, installation: PluginInstallation, request: PluginCapabilityBinding
) -> PluginBinding:
    """Create or confirm one resource owner in the caller's transaction, without committing."""
    release = session.scalar(
        select(PluginRelease).where(
            PluginRelease.workspace_id == installation.workspace_id,
            PluginRelease.id == installation.current_release_id,
        )
    )
    if release is None:
        raise NotFoundError("Plugin release not found")
    capability = release_capabilities(release).get(request.capability_key)
    if capability is None:
        raise ValueError("Unknown plugin capability")
    configuration, revision = validate_binding(
        session, installation, capability, request.resource_id
    )
    binding = session.scalar(
        select(PluginBinding)
        .where(
            PluginBinding.workspace_id == installation.workspace_id,
            PluginBinding.kind == capability.kind,
            PluginBinding.resource_id == request.resource_id,
        )
        .with_for_update()
    )
    if binding is not None:
        if binding.install_id != installation.id or binding.capability_key != capability.key:
            raise ConflictError("Resource already belongs to another plugin capability")
        if binding.configuration_revision != revision:
            raise ConflictError("Changed resource requires explicit binding reauthorization")
        return binding
    binding = PluginBinding(
        workspace_id=installation.workspace_id,
        install_id=installation.id,
        capability_key=capability.key,
        kind=capability.kind,
        resource_id=request.resource_id,
        configuration=configuration,
        configuration_revision=revision,
    )
    session.add(binding)
    flush_or_raise_conflict(session, "Resource already belongs to another plugin capability")
    return binding


def reauthorize_binding(
    session: Session, installation: PluginInstallation, binding_id: UUID, expected_revision: int
) -> PluginBinding:
    """Authorize exactly the revision an administrator reviewed; caller owns audit and commit."""
    binding = session.scalar(
        select(PluginBinding)
        .where(
            PluginBinding.workspace_id == installation.workspace_id,
            PluginBinding.install_id == installation.id,
            PluginBinding.id == binding_id,
        )
        .with_for_update()
    )
    if binding is None:
        raise NotFoundError("Plugin binding not found")
    release = session.get(PluginRelease, installation.current_release_id)
    if release is None or release.workspace_id != installation.workspace_id:
        raise NotFoundError("Plugin release not found")
    capability = release_capabilities(release).get(binding.capability_key)
    if capability is None or capability.kind != binding.kind:
        raise ConflictError("Plugin capability no longer exists")
    configuration, revision = validate_binding(
        session, installation, capability, binding.resource_id
    )
    if revision != expected_revision:
        raise ConflictError("Resource configuration changed; review the current revision")
    binding.configuration = configuration
    binding.configuration_revision = revision
    return binding
