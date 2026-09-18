"""The single live gate for plugin-owned capabilities, including frozen run references."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import PolicyDeniedError
from backend.app.domains.capabilities.mcp.models import McpServer
from backend.app.domains.capabilities.plugins.models import (
    PluginBinding,
    PluginInstall,
    PluginRelease,
    PluginTrustKey,
)
from backend.app.domains.capabilities.skills.models import WorkspaceSkillInstall
from backend.app.domains.integrations.automation_models import Automation
from backend.app.domains.integrations.webhooks.models import WebhookSubscription


def resource_configuration(
    session: Session,
    workspace_id: UUID,
    kind: str,
    resource_id: UUID,
) -> dict[str, object]:
    """Capture execution configuration, not transient health, review or credential material."""
    if kind == "mcp_server":
        server = session.scalar(
            select(McpServer)
            .where(
                McpServer.workspace_id == workspace_id,
                McpServer.id == resource_id,
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
        return {
            "manifest": skill.installed_manifest,
            "config": skill.config,
            "version": skill.installed_version,
            "checksum": skill.source_checksum,
        }
    if kind == "message_trigger":
        automation = session.scalar(
            select(Automation)
            .where(
                Automation.workspace_id == workspace_id,
                Automation.id == resource_id,
            )
            .execution_options(populate_existing=True)
        )
        if automation is None or automation.configuration.get("trigger_type") != "message":
            raise ValueError("Plugin binding requires a message automation")
        return dict(automation.configuration)
    if kind == "reply_channel":
        subscription = session.scalar(
            select(WebhookSubscription)
            .where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == resource_id,
            )
            .execution_options(populate_existing=True)
        )
        if subscription is None:
            raise ValueError("Plugin reply subscription not found")
        return {"target_url": subscription.target_url, "event_types": subscription.event_types}
    raise ValueError("Unsupported plugin capability kind")


def plugin_resource_available(
    session: Session,
    workspace_id: UUID,
    kind: str,
    resource_id: UUID,
) -> bool:
    row = session.execute(
        select(PluginBinding, PluginInstall, PluginRelease, PluginTrustKey)
        .join(PluginInstall, PluginInstall.id == PluginBinding.install_id)
        .join(PluginRelease, PluginRelease.id == PluginBinding.release_id)
        .join(PluginTrustKey, PluginTrustKey.id == PluginRelease.trust_key_id)
        .where(
            PluginBinding.workspace_id == workspace_id,
            PluginBinding.kind == kind,
            PluginBinding.resource_id == resource_id,
            PluginInstall.workspace_id == workspace_id,
            PluginRelease.workspace_id == workspace_id,
            PluginTrustKey.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        return True
    binding, install, release, key = row
    if install.status != "active" or release.status != "available" or key.status != "active":
        return False
    try:
        return bool(
            binding.configuration
            == resource_configuration(session, workspace_id, kind, resource_id)
        )
    except ValueError:
        return False


def require_plugin_resource(
    session: Session,
    workspace_id: UUID,
    kind: str,
    resource_id: UUID,
) -> None:
    if not plugin_resource_available(session, workspace_id, kind, resource_id):
        raise PolicyDeniedError(
            "Plugin capability is disabled, revoked or changed", code="plugin_unavailable"
        )
