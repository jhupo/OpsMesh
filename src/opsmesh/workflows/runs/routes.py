"""Workflow submission and durable controls through the configured engine port."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http.dependencies import secret_cipher
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.workflows.engines.base import WorkflowEngine
from opsmesh.workflows.engines.registry import EngineRegistry
from opsmesh.workflows.runs.access import get_run
from opsmesh.workflows.runs.control import cancel_run, decide_approval, deliver_signal
from opsmesh.workflows.runs.models import (
    WorkflowEvent,
    WorkflowNodeRun,
    WorkflowOperation,
    WorkflowRun,
)
from opsmesh.workflows.runs.operations import reconcile_effect, settle_run
from opsmesh.workflows.runs.schemas import (
    ApprovalDecision,
    EffectResolution,
    NodeView,
    RunCreate,
    RunView,
    SignalCreate,
)
from opsmesh.workflows.runs.service import submit_run

router = APIRouter(prefix="/workflows/runs", tags=["workflow-runs"])


def engine(request: Request, session: Session, name: str) -> WorkflowEngine:
    registry = getattr(request.app.state, "workflow_engines", None)
    if not isinstance(registry, EngineRegistry):
        raise HTTPException(503, "Workflow engines are not configured")
    return registry.get(name, session)


@router.post("", status_code=202, operation_id="workflows.runs.create")
def create(
    payload: RunCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> RunView:
    adapter = engine(request, session, payload.engine)
    with session_transaction(session):
        row = submit_run(session, actor=user, request=payload, cipher=secret_cipher(request))
        if row.status == "queued":
            adapter.start(row.id)
        return RunView.model_validate(row)


@router.get("", operation_id="workflows.runs.list")
def list_runs(
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[RunView]:
    return [
        RunView.model_validate(row)
        for row in session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.user_id == user.user_id)
            .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/{run_id}", operation_id="workflows.runs.read")
def read(run_id: UUID, session: DbSession, user: CurrentUserDep) -> RunView:
    return RunView.model_validate(get_run(session, actor=user, run_id=run_id))


@router.post("/{run_id}/cancel", operation_id="workflows.runs.cancel")
def cancel(run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request) -> RunView:
    with session_transaction(session):
        cancel_run(session, actor=user, run_id=run_id, cipher=secret_cipher(request))
        row = get_run(session, actor=user, run_id=run_id)
        if row.status == "cancelling":
            engine(request, session, row.engine).cancel(run_id)
        return RunView.model_validate(row)


@router.post("/{run_id}/signals", operation_id="workflows.runs.signal")
def signal(
    run_id: UUID, payload: SignalCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    with session_transaction(session):
        row = get_run(session, actor=user, run_id=run_id)
        value = deliver_signal(
            session, actor=user, run_id=run_id, request=payload, cipher=secret_cipher(request)
        )
        engine(request, session, row.engine).signal(run_id, value.id)
        return {"id": str(value.id), "status": value.status}


@router.get("/{run_id}/result", operation_id="workflows.runs.result")
def result(
    run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    row = get_run(session, actor=user, run_id=run_id)
    if row.encrypted_result is None:
        raise HTTPException(409, "The workflow has no confirmed result")
    return secret_cipher(request).decrypt_payload(row.encrypted_result)


@router.get("/{run_id}/nodes", operation_id="workflows.runs.nodes")
def nodes(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[NodeView]:
    get_run(session, actor=user, run_id=run_id)
    return [
        NodeView.model_validate(row)
        for row in session.scalars(
            select(WorkflowNodeRun)
            .where(WorkflowNodeRun.run_id == run_id)
            .order_by(WorkflowNodeRun.created_at, WorkflowNodeRun.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.post("/{run_id}/nodes/{node_id}/approval", operation_id="workflows.runs.approve")
def approve(
    run_id: UUID,
    node_id: UUID,
    payload: ApprovalDecision,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> NodeView:
    with session_transaction(session):
        node = decide_approval(
            session,
            actor=user,
            run_id=run_id,
            node_id=node_id,
            request=payload,
            cipher=secret_cipher(request),
        )
        row = get_run(session, actor=user, run_id=run_id)
        engine(request, session, row.engine).signal(run_id, payload.approval_id)
        return NodeView.model_validate(node)


@router.get("/{run_id}/nodes/{node_id}", operation_id="workflows.runs.node")
def node_details(
    run_id: UUID, node_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    get_run(session, actor=user, run_id=run_id)
    node = session.get(WorkflowNodeRun, node_id)
    if node is None or node.run_id != run_id:
        raise NotFoundError("The workflow node does not exist")
    cipher = secret_cipher(request)
    return {
        "node": NodeView.model_validate(node).model_dump(mode="json"),
        "input": cipher.decrypt_payload(node.encrypted_input),
        "descriptor": cipher.decrypt_payload(node.encrypted_descriptor)
        if node.encrypted_descriptor
        else None,
        "result": cipher.decrypt_payload(node.encrypted_result) if node.encrypted_result else None,
    }


@router.get("/{run_id}/events", operation_id="workflows.runs.events")
def events(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, object]]:
    get_run(session, actor=user, run_id=run_id)
    cipher = secret_cipher(request)
    return [
        {
            "sequence": row.sequence,
            "kind": row.kind,
            "payload": cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id),
        }
        for row in session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == run_id, WorkflowEvent.sequence > after)
            .order_by(WorkflowEvent.sequence)
            .limit(limit)
        )
    ]


@router.get("/{run_id}/effects", operation_id="workflows.runs.effects")
def effects(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, object]]:
    get_run(session, actor=user, run_id=run_id)
    return [
        {"id": row.id, "node_id": str(row.node_id), "status": row.status}
        for row in session.scalars(
            select(WorkflowOperation)
            .where(WorkflowOperation.run_id == run_id)
            .order_by(WorkflowOperation.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.post("/{run_id}/reconciliation", operation_id="workflows.runs.reconcile")
def reconcile(
    run_id: UUID,
    payload: EffectResolution,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> RunView:
    with session_transaction(session):
        return RunView.model_validate(
            reconcile_effect(
                session, actor=user, run_id=run_id, request=payload, cipher=secret_cipher(request)
            )
        )


@router.post("/{run_id}/settle", operation_id="workflows.runs.settle")
def settle(run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request) -> RunView:
    with session_transaction(session):
        return RunView.model_validate(
            settle_run(session, actor=user, run_id=run_id, cipher=secret_cipher(request))
        )
