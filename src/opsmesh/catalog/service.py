from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import and_, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from opsmesh.agents.models import AgentInstance
from opsmesh.contracts import UserIdentity
from opsmesh.contracts.manifest import DependencyReference, PluginManifest
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import load
from opsmesh.plugins.models import Plugin
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.models import User
from opsmesh.users.service import require_owner

from .models import CatalogPublication
from .schemas import PublishResourceRequest


def publish_resource(
    db: Session,
    cipher: SecretCipher,
    registry: PluginRegistry,
    root: Path,
    user: UserIdentity,
    payload: PublishResourceRequest,
) -> CatalogPublication:
    if not set(payload.group_ids) <= user.group_ids and not user.is_admin:
        raise ValueError("Publication groups must be your groups")
    if payload.visibility == "group" and not payload.group_ids:
        raise ValueError("Group publication requires groups")
    owner_id = user.user_id
    if payload.kind == "agent":
        if payload.plugin_id or payload.contribution or payload.config or payload.dependencies:
            raise ValueError("Agent publications use the existing instance configuration")
        agent = db.get(AgentInstance, payload.agent_id)
        if agent is None or not agent.enabled:
            raise NotFoundError("Agent unavailable")
        require_owner(user, agent.owner_id)
        owner_id, plugin_id, contribution = agent.owner_id, agent.plugin_id, agent.entry
        config, pins = agent.encrypted_config, agent.dependencies
        authorize_pins(db, user, pins)
    else:
        if payload.plugin_id is None or payload.agent_id is not None:
            raise ValueError("A plugin installation is required")
        plugin = require_plugin_access(db, user, payload.plugin_id)
        context = load(db, root, registry, plugin.id)
        registries: dict[str, Mapping[str, Any]] = {
            "tool": context.tools,
            "mcp": context.mcps,
            "skill": context.skills,
        }
        if payload.kind != "plugin" and payload.contribution not in registries[payload.kind]:
            raise NotFoundError("Plugin contribution not found")
        if payload.kind == "plugin" and (payload.config or payload.contribution):
            raise ValueError(
                "Plugin publications describe the package; publish configured contributions separately"
            )
        plugin_id, contribution = plugin.id, payload.contribution
        config = cipher.encrypt(payload.config)
        declared = PluginManifest.model_validate(plugin.manifest).dependencies
        if declared.keys() & payload.dependencies.keys():
            raise ValueError("Publication cannot override plugin dependency declarations")
        pins = resolve(db, user, declared | payload.dependencies)
    row = CatalogPublication(
        owner_id=owner_id,
        name=payload.name,
        version=payload.version,
        kind=payload.kind,
        plugin_id=plugin_id,
        contribution=contribution,
        description=payload.description,
        visibility=payload.visibility,
        group_ids=payload.group_ids,
        encrypted_config=config,
        dependencies=pins,
    )
    db.add(row)
    db.flush()
    return row


def discover(
    db: Session,
    user: UserIdentity,
    *,
    kind: str | None,
    name: str | None,
    owner_id: str | None,
    version: str | None,
    offset: int,
    limit: int,
) -> list[CatalogPublication]:
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid pagination")
    query = (
        select(CatalogPublication)
        .join(User, User.id == CatalogPublication.owner_id)
        .join(Plugin, Plugin.id == CatalogPublication.plugin_id)
        .where(
            CatalogPublication.enabled.is_(True), User.enabled.is_(True), Plugin.enabled.is_(True)
        )
    )
    if not user.is_admin:
        visible = [
            CatalogPublication.owner_id == user.user_id,
            CatalogPublication.visibility == "public",
        ]
        visible.extend(
            and_(
                CatalogPublication.visibility == "group",
                cast(CatalogPublication.group_ids, JSONB).contains([group]),
            )
            for group in user.group_ids
        )
        query = query.where(or_(*visible))
    for column, value in [
        (CatalogPublication.kind, kind),
        (CatalogPublication.name, name),
        (CatalogPublication.owner_id, owner_id),
        (CatalogPublication.version, version),
    ]:
        if value is not None:
            query = query.where(column == value)
    return list(
        db.scalars(
            query.order_by(CatalogPublication.created_at.desc(), CatalogPublication.id)
            .offset(offset)
            .limit(limit)
        )
    )


def accessible(db: Session, user: UserIdentity, row: CatalogPublication) -> bool:
    if not row.enabled:
        return False
    if user.is_admin or user.user_id == row.owner_id:
        return True
    owner = db.get(User, row.owner_id)
    if owner is None or not owner.enabled:
        return False
    return row.visibility == "public" or (
        row.visibility == "group" and bool(user.group_ids.intersection(row.group_ids))
    )


def get(db: Session, user: UserIdentity, publication_id: str) -> CatalogPublication:
    row = db.get(CatalogPublication, publication_id)
    if row is None or not accessible(db, user, row):
        raise NotFoundError("CatalogPublication unavailable")
    plugin = db.get(Plugin, row.plugin_id)
    if plugin is None or not plugin.enabled:
        raise NotFoundError("CatalogPublication plugin unavailable")
    return row


def require_plugin_access(db: Session, user: UserIdentity, plugin_id: str) -> Plugin:
    row = db.get(Plugin, plugin_id)
    if row is None or not row.enabled:
        raise NotFoundError("Plugin unavailable")
    if user.is_admin or row.owner_id == user.user_id:
        return row
    for published in db.scalars(
        select(CatalogPublication).where(
            CatalogPublication.plugin_id == row.id,
            CatalogPublication.kind == "plugin",
            CatalogPublication.enabled.is_(True),
        )
    ):
        if accessible(db, user, published):
            return row
    raise NotFoundError("Plugin unavailable")


def resolve(
    db: Session,
    user: UserIdentity,
    dependencies: dict[str, DependencyReference],
    trail: tuple[str, ...] = (),
) -> dict[str, Any]:
    if len(trail) > 12:
        raise ConflictError("DependencyReference graph is too deep")
    result = {}
    for alias, dependency in dependencies.items():
        row = db.scalar(
            select(CatalogPublication).where(
                CatalogPublication.id == dependency.id,
                CatalogPublication.version == dependency.version,
                CatalogPublication.kind == dependency.kind,
            )
        )
        if row is None:
            raise NotFoundError(f"DependencyReference unavailable: {alias}")
        get(db, user, row.id)
        if row.id in trail:
            raise ConflictError("DependencyReference cycle detected")
        result[alias] = {"id": row.id, "kind": row.kind, "version": row.version}
    authorize_pins(db, user, result, trail)
    return result


def authorize_pins(
    db: Session,
    user: UserIdentity,
    pins: dict[str, Any],
    trail: tuple[str, ...] = (),
    *,
    seen: set[str] | None = None,
) -> None:
    if len(trail) > 12:
        raise ConflictError("DependencyReference graph is too deep")
    visited = seen if seen is not None else set()
    for pin in pins.values():
        row = get(db, user, pin["id"])
        if row.id in trail or row.version != pin["version"] or row.kind != pin["kind"]:
            raise ConflictError("Invalid pinned dependency")
        if row.id in visited:
            continue
        visited.add(row.id)
        if len(visited) > 100:
            raise ConflictError("Dependency graph exceeds 100 publications")
        authorize_pins(db, user, row.dependencies, (*trail, row.id), seen=visited)
