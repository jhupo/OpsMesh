"""The single live gate for plugin-owned capabilities, including frozen run references."""

from uuid import UUID

from opsmesh_plugin_sdk.packaging.packages import SignedPluginPackage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.models import MCPServer
from opsmesh.capabilities.plugins.models import (
    PluginBinding,
    PluginInstallation,
    PluginRelease,
    PluginTrustKey,
)
from opsmesh.capabilities.skills.models import Skill, WorkspaceSkillInstall
from opsmesh.shared.errors import PolicyDeniedError


def resource_configuration(
    session: Session,
    workspace_id: UUID,
    kind: str,
    resource_id: UUID,
) -> dict[str, object]:
    """Capture execution configuration, not transient health, review or credential material."""
    if kind == "mcp_server":
        server = session.scalar(
            select(MCPServer)
            .where(
                MCPServer.workspace_id == workspace_id,
                MCPServer.id == resource_id,
            )
            .execution_options(populate_existing=True)
        )
        if server is None or server.server_type not in {"streamable_http", "sse", "hosted"}:
            raise ValueError("Plugin binding requires a workspace remote MCP server")
        return {"server_type": server.server_type, "connection": server.connection}
    if kind == "skill":
        skill = session.scalar(
            select(WorkspaceSkillInstall)
            .where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.id == resource_id,
            )
            .execution_options(populate_existing=True)
        )
        if skill is None:
            raise ValueError("Plugin skill binding not found")
        source = session.scalar(
            select(Skill).where(Skill.id == skill.skill_id, Skill.platform_blocked.is_(False))
        )
        if skill.platform_blocked or source is None:
            raise ValueError("Plugin skill binding is blocked by the platform")
        return {
            "manifest": skill.installed_manifest,
            "config": skill.config,
            "version": skill.installed_version,
            "checksum": skill.source_checksum,
        }
    if kind == "channel":
        from opsmesh.messaging.channels.models import ChannelConnection

        connection = session.scalar(
            select(ChannelConnection)
            .where(
                ChannelConnection.workspace_id == workspace_id,
                ChannelConnection.id == resource_id,
            )
            .execution_options(populate_existing=True)
        )
        if connection is None:
            raise ValueError("Plugin channel connection not found")
        return dict(connection.configuration)
    raise ValueError("Unsupported plugin capability kind")


def resource_configuration_revision(
    session: Session, workspace_id: UUID, kind: str, resource_id: UUID
) -> int:
    """Read the product revision used for explicit binding reauthorization."""
    if kind == "mcp_server":
        table, column = MCPServer.__table__, "configuration_version"
    elif kind == "skill":
        table, column = WorkspaceSkillInstall.__table__, "configuration_revision"
    elif kind == "channel":
        from opsmesh.messaging.channels.models import ChannelConnection

        table, column = ChannelConnection.__table__, "configuration_revision"
    else:
        raise ValueError("Unsupported plugin capability kind")
    query = select(table.c[column]).where(
        table.c.workspace_id == workspace_id, table.c.id == resource_id
    )
    if kind == "channel":
        query = query.where(table.c.status == "active")
    revision = session.connection().scalar(query)
    if revision is None:
        raise ValueError("Plugin binding resource not found or disabled")
    return int(revision)


def plugin_resource_available(
    session: Session,
    workspace_id: UUID,
    kind: str,
    resource_id: UUID,
    *,
    required_feature: str | None = None,
) -> bool:
    """Read tenant policy facts independently of the caller's administrative visibility.

    Resource authorization is checked by the caller. This gate grants no data access;
    it only prevents execution after installation, release or binding revocation.
    Core table expressions avoid user ORM filters hiding policy rows and confusing
    an inaccessible plugin administration record with an absent resource binding.
    """
    binding = PluginBinding.__table__
    installation = PluginInstallation.__table__
    release = PluginRelease.__table__
    key = PluginTrustKey.__table__
    row = (
        session.connection()
        .execute(
            select(binding, installation, release, key)
            .join(installation, installation.c.id == binding.c.install_id)
            .join(release, release.c.id == installation.c.current_release_id)
            .join(key, key.c.id == release.c.trust_key_id)
            .where(
                binding.c.workspace_id == workspace_id,
                binding.c.kind == kind,
                binding.c.resource_id == resource_id,
                installation.c.workspace_id == workspace_id,
                release.c.workspace_id == workspace_id,
                key.c.workspace_id == workspace_id,
                key.c.plugin_key == installation.c.plugin_key,
                release.c.plugin_key == installation.c.plugin_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return (
            session.connection().scalar(
                select(binding.c.id).where(
                    binding.c.workspace_id == workspace_id,
                    binding.c.kind == kind,
                    binding.c.resource_id == resource_id,
                )
            )
            is None
            and kind != "channel"
        )
    if (
        row[installation.c.status] != "active"
        or row[installation.c.platform_blocked]
        or row[release.c.status] != "available"
        or row[key.c.status] != "active"
    ):
        return False
    try:
        manifest = SignedPluginPackage.model_validate(row[release.c.package]).manifest
        capability = next(
            (item for item in manifest.capabilities if item.key == row[binding.c.capability_key]),
            None,
        )
        if (
            capability is None
            or capability.kind != kind
            or (required_feature is not None and required_feature not in capability.features)
            or not set(capability.required_permissions).issubset(
                row[installation.c.approved_permissions]
            )
        ):
            return False
        return bool(
            row[binding.c.configuration_revision]
            == resource_configuration_revision(session, workspace_id, kind, resource_id)
        )
    except ValueError:
        return False


def require_plugin_resource(
    session: Session,
    workspace_id: UUID,
    kind: str,
    resource_id: UUID,
    *,
    required_feature: str | None = None,
) -> None:
    if not plugin_resource_available(
        session, workspace_id, kind, resource_id, required_feature=required_feature
    ):
        raise PolicyDeniedError(
            "Plugin capability is disabled, revoked or changed", code="plugin_unavailable"
        )
