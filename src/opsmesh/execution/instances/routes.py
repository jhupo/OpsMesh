"""Persistent Agent instance HTTP API."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from opsmesh.execution.instances.access import get_instance
from opsmesh.execution.instances.models import AgentInstance
from opsmesh.execution.instances.schemas import InstanceCreate, InstanceView
from opsmesh.execution.instances.service import create_instance, delete_instance
from opsmesh.execution.runtime.routes import secret_cipher
from opsmesh.shared.db.session import session_transaction
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/execution/instances", tags=["execution-instances"])


@router.post("", status_code=201, operation_id="execution.instances.create")
def create(
    payload: InstanceCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> InstanceView:
    load = getattr(request.app.state, "execution_definition_loader", None)
    if load is None:
        raise HTTPException(503, "The Agent definition catalog is not configured")
    with session_transaction(session):
        return InstanceView.model_validate(
            create_instance(
                session,
                actor=user,
                request=payload,
                load=load(session, user),
                cipher=secret_cipher(request),
            )
        )


@router.get("", operation_id="execution.instances.list")
def list_instances(
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[InstanceView]:
    return [
        InstanceView.model_validate(row)
        for row in session.scalars(
            select(AgentInstance)
            .where(
                AgentInstance.owner_user_id == user.user_id,
                AgentInstance.status != "deleted",
            )
            .order_by(AgentInstance.created_at.desc(), AgentInstance.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/{instance_id}", operation_id="execution.instances.read")
def read(instance_id: UUID, session: DbSession, user: CurrentUserDep) -> InstanceView:
    return InstanceView.model_validate(get_instance(session, actor=user, instance_id=instance_id))


@router.delete("/{instance_id}", status_code=204, operation_id="execution.instances.delete")
def delete(instance_id: UUID, session: DbSession, user: CurrentUserDep) -> None:
    with session_transaction(session):
        delete_instance(session, actor=user, instance_id=instance_id)
