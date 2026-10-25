from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from opsmesh.catalog.service import authorize_pins, require_plugin_access, resolve
from opsmesh.contracts import UserIdentity
from opsmesh.contracts.manifest import PluginManifest
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import load
from opsmesh.plugins.models import Plugin
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.resources.workspaces import owned
from opsmesh.shared.errors import NotFoundError
from opsmesh.users.models import GroupMember, User
from opsmesh.users.service import can_use, identity

from .models import AgentInstance
from .schemas import CreateAgentRequest


def create_agent(
    db: Session,
    cipher: SecretCipher,
    registry: PluginRegistry,
    root: Path,
    user: UserIdentity,
    payload: CreateAgentRequest,
) -> AgentInstance:
    plugin = require_plugin_access(db, user, payload.plugin_id)
    context = load(db, root, registry, plugin.id)
    if payload.entry not in context.entries:
        raise NotFoundError("Plugin entry not found")
    if payload.workspace_id:
        workspace = owned(db, user, payload.workspace_id)
        if workspace.owner_id != user.user_id:
            raise ValueError("Agent workspace must belong to its owner")
    declared = PluginManifest.model_validate(plugin.manifest).dependencies
    overlap = declared.keys() & payload.dependencies.keys()
    if overlap:
        raise ValueError("Instance dependencies cannot override plugin declarations")
    row = AgentInstance(
        owner_id=user.user_id,
        name=payload.name,
        plugin_id=plugin.id,
        entry=payload.entry,
        encrypted_config=cipher.encrypt(payload.config),
        dependencies=resolve(db, user, declared | payload.dependencies),
        workspace_id=payload.workspace_id,
    )
    db.add(row)
    db.flush()
    return row


def list_agents(db: Session, user: UserIdentity, offset: int, limit: int) -> list[AgentInstance]:
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid pagination")
    query = select(AgentInstance).where(AgentInstance.deleted_at.is_(None))
    if not user.is_admin:
        owners = select(GroupMember.user_id).where(GroupMember.group_id.in_(user.group_ids))
        query = query.join(User, User.id == AgentInstance.owner_id).where(
            User.enabled.is_(True),
            or_(AgentInstance.owner_id == user.user_id, AgentInstance.owner_id.in_(owners)),
        )
    return list(
        db.scalars(
            query.order_by(AgentInstance.created_at.desc(), AgentInstance.id)
            .offset(offset)
            .limit(limit)
        )
    )


def usable(db: Session, user: UserIdentity, agent_id: str) -> AgentInstance:
    row = db.scalar(select(AgentInstance).where(AgentInstance.id == agent_id).with_for_update())
    if row is None or row.deleted_at or not row.enabled or not can_use(db, user, row.owner_id):
        raise NotFoundError("Agent unavailable")
    identity(db, row.owner_id)
    plugin = db.get(Plugin, row.plugin_id)
    if plugin is None or not plugin.enabled:
        raise NotFoundError("Agent plugin unavailable")
    authorize_pins(db, user, row.dependencies)
    return row


def snapshot(row: AgentInstance) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_id": row.owner_id,
        "plugin_id": row.plugin_id,
        "entry": row.entry,
        "encrypted_config": row.encrypted_config,
        "dependencies": row.dependencies,
        "workspace_id": row.workspace_id,
    }
