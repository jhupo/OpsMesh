"""Owner-scoped workflow instances; deletion never cascades to execution resources."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import secret_cipher
from opsmesh.shared.security.redaction import RedactionRules
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.workflows.instances.access import get_instance
from opsmesh.workflows.instances.models import WorkflowInstance
from opsmesh.workflows.instances.schemas import InstanceCreate, InstanceView
from opsmesh.workflows.instances.service import create_instance, set_status

router = APIRouter(prefix="/workflows/instances", tags=["workflow-instances"])


class InstanceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"]


@router.post("", status_code=201, operation_id="workflows.instances.create")
def create(
    payload: InstanceCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> InstanceView:
    rules = getattr(request.app.state, "redaction_rules", None)
    if not isinstance(rules, RedactionRules):
        raise HTTPException(503, "Workflow binding rules are not configured")
    with session_transaction(session):
        return InstanceView.model_validate(
            create_instance(
                session, actor=user, request=payload, cipher=secret_cipher(request), redaction=rules
            )
        )


@router.get("", operation_id="workflows.instances.list")
def list_instances(
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[InstanceView]:
    return [
        InstanceView.model_validate(row)
        for row in session.scalars(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.owner_user_id == user.user_id, WorkflowInstance.status != "deleted"
            )
            .order_by(WorkflowInstance.created_at.desc(), WorkflowInstance.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/{instance_id}", operation_id="workflows.instances.read")
def read(instance_id: UUID, session: DbSession, user: CurrentUserDep) -> InstanceView:
    return InstanceView.model_validate(get_instance(session, actor=user, instance_id=instance_id))


@router.patch("/{instance_id}", operation_id="workflows.instances.update")
def update(
    instance_id: UUID, payload: InstanceStatus, session: DbSession, user: CurrentUserDep
) -> InstanceView:
    with session_transaction(session):
        return InstanceView.model_validate(
            set_status(session, actor=user, instance_id=instance_id, status=payload.status)
        )


@router.delete("/{instance_id}", operation_id="workflows.instances.delete")
def delete(instance_id: UUID, session: DbSession, user: CurrentUserDep) -> InstanceView:
    with session_transaction(session):
        return InstanceView.model_validate(
            set_status(session, actor=user, instance_id=instance_id, status="deleted")
        )
