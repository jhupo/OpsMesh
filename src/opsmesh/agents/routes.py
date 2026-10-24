from typing import Any

from fastapi import APIRouter, Depends, Request
from opsmesh_sdk import UserIdentity
from opsmesh_sdk.manifest import PluginManifest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from opsmesh.catalog.service import resolve
from opsmesh.gateway.models import ChannelConfiguration
from opsmesh.infra.database import now
from opsmesh.plugins.models import Plugin
from opsmesh.resources.workspaces import owned
from opsmesh.sessions.models import AgentSession
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.tasks.models import Task, TaskSchedule
from opsmesh.users.service import identity, require_owner

from . import service
from .models import AgentInstance
from .schemas import AgentDetailsView, AgentView, CreateAgentRequest, UpdateAgentRequest

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", status_code=201, response_model=AgentView)
def create_agent(
    body: CreateAgentRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    app = get_application(request)
    row = service.create_agent(db, app.cipher, app.registry, app.settings.data_dir, user, body)
    return serialize_record(row)


@router.get("", response_model=list[AgentView])
def listing(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [
        serialize_record(row, exclude=("workspace_id", "dependencies"))
        for row in service.list_agents(db, user, offset, limit)
    ]


@router.get("/{agent_id}", response_model=AgentDetailsView)
def detail(
    agent_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = service.usable(db, user, agent_id)
    result = serialize_record(row, exclude=("workspace_id", "dependencies"))
    if row.owner_id == user.user_id or user.is_admin:
        result.update(
            config=get_application(request).cipher.decrypt(row.encrypted_config),
            workspace_id=row.workspace_id,
            dependencies=row.dependencies,
        )
    return result


@router.patch("/{agent_id}", response_model=AgentView)
def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = db.get(AgentInstance, agent_id, with_for_update=True)
    if row is None or row.deleted_at:
        raise NotFoundError("Agent not found")
    require_owner(user, row.owner_id)
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for key, value in changes.items() if key != "workspace_id"):
        raise ValueError("Agent fields other than workspace_id cannot be null")
    if "config" in changes:
        row.encrypted_config = get_application(request).cipher.encrypt(changes.pop("config"))
    if "dependencies" in changes:
        plugin = db.get(Plugin, row.plugin_id)
        assert plugin is not None
        declared = PluginManifest.model_validate(plugin.manifest).dependencies
        dependencies = body.dependencies
        assert dependencies is not None
        if declared.keys() & dependencies.keys():
            raise ValueError("Instance dependencies cannot override plugin declarations")
        owner = identity(db, row.owner_id)
        row.dependencies = resolve(db, owner, declared | dependencies)
        changes.pop("dependencies")
    if changes.get("workspace_id"):
        workspace = owned(db, user, changes["workspace_id"])
        if workspace.owner_id != row.owner_id:
            raise ValueError("Agent workspace must belong to its owner")
    for key, value in changes.items():
        setattr(row, key, value)
    return serialize_record(row)


@router.delete("/{agent_id}", status_code=204)
def delete_agent(
    agent_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    row = db.get(AgentInstance, agent_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Agent not found")
    require_owner(user, row.owner_id)
    if row.deleted_at:
        return
    if db.scalar(
        select(Task.id)
        .where(Task.agent_id == agent_id, Task.status.in_(["queued", "running", "waiting_input"]))
        .limit(1)
    ):
        raise ConflictError("Cancel or finish the agent's active tasks before deleting it")
    row.deleted_at, row.enabled = now(), False
    db.execute(
        update(ChannelConfiguration)
        .where(ChannelConfiguration.agent_id == agent_id)
        .values(enabled=False, revision=ChannelConfiguration.revision + 1)
    )
    db.execute(update(TaskSchedule).where(TaskSchedule.agent_id == agent_id).values(enabled=False))
    for conversation in db.scalars(
        select(AgentSession).where(AgentSession.agent_key == agent_id).with_for_update()
    ):
        if conversation.sandbox_access_until and conversation.sandbox_access_until > now():
            raise ConflictError("Finish sandbox file operations before deleting its agent")
        conversation.closed = True
