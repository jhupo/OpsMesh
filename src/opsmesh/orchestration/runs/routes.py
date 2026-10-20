"""Composition-owned state and explicit execution operations."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.responses import StreamingResponse

from opsmesh.identity.auth.dependencies import (
    INSTALLATION_HEADER,
    INSTANCE_HEADER,
    INVOCATION_HEADER,
    NAMESPACE_HEADER,
    SESSION_HEADER,
    CurrentUserDep,
    DbSession,
    current_user,
    get_auth_service,
    get_delegation_resolver,
)
from opsmesh.orchestration.access import authorize_scope
from opsmesh.orchestration.contracts import ExecutionBindings, ExecutionSubject
from opsmesh.orchestration.definitions.contracts import FlowSignal
from opsmesh.orchestration.dependencies import execution_cipher as cipher_of
from opsmesh.orchestration.dependencies import flow_cipher, flow_runtime
from opsmesh.orchestration.dependencies import scope_admission as _scope_admission
from opsmesh.orchestration.execution.contracts import ExecutionRequest
from opsmesh.orchestration.runs import reconciliation, service
from opsmesh.orchestration.runs.access import load_flow_run, load_run
from opsmesh.orchestration.runs.events import read_events
from opsmesh.orchestration.runs.models import FlowEvent, FlowRun, OperationReceipt, Run, StepAttempt
from opsmesh.orchestration.runs.schemas import (
    CancelRequest,
    EventPage,
    FlowControl,
    FlowEventView,
    FlowPlanProposal,
    FlowPlanReview,
    FlowPlanReviewView,
    FlowRunCreate,
    FlowRunView,
    FlowStepView,
    OperationView,
    ReconcileRequest,
    ResumeRequest,
    RunView,
)
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt
from opsmesh.orchestration.sessions.access import load_visible_session
from opsmesh.orchestration.sessions.models import SessionOperationReceipt
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import DomainError

flow_router = APIRouter(prefix="/orchestration/flows", tags=["orchestration"])


@flow_router.post(
    "", response_model=FlowRunView, status_code=202, operation_id="orchestration.flows.create"
)
def create(
    payload: FlowRunCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: str = Query(min_length=1, max_length=150),
) -> FlowRunView:
    with session_transaction(session):
        record = service.start_run(
            session,
            actor=user,
            request=payload,
            idempotency_key=idempotency_key,
            cipher=flow_cipher(request),
        )
        flow_runtime(request).validate_start(session, actor=user, run=record)
    return service.flow_run_view(record, flow_cipher(request))


@flow_router.get("", response_model=list[FlowRunView], operation_id="orchestration.flows.list")
def list_flow_runs(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    configuration_id: UUID | None = None,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FlowRunView]:
    authorize_scope(
        session,
        actor=user,
        owner_id=user.user_id,
        configuration_id=configuration_id,
    )
    query = select(FlowRun).where(
        FlowRun.configuration_id == configuration_id,
        FlowRun.principal_id == user.user_id,
    )
    if after:
        query = query.where(FlowRun.id > after)
    return [
        service.flow_run_view(record, flow_cipher(request))
        for record in session.scalars(query.order_by(FlowRun.id).limit(limit))
    ]


@flow_router.get("/{run_id}", response_model=FlowRunView, operation_id="orchestration.flows.get")
def get(run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request) -> FlowRunView:
    return service.flow_run_view(
        load_flow_run(session, actor=user, run_id=run_id), flow_cipher(request)
    )


@flow_router.post(
    "/{run_id}/control", response_model=FlowRunView, operation_id="orchestration.flows.control"
)
def control(
    payload: FlowControl, run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> FlowRunView:
    with session_transaction(session):
        record = service.control_run(
            session, actor=user, run_id=run_id, request=payload, cipher=flow_cipher(request)
        )
    return service.flow_run_view(record, flow_cipher(request))


@flow_router.post(
    "/{run_id}/signals",
    response_model=FlowRunView,
    operation_id="orchestration.flows.signals.submit",
)
def signal(
    payload: FlowSignal, run_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> FlowRunView:
    with session_transaction(session):
        record = service.submit_signal(
            session, actor=user, run_id=run_id, request=payload, cipher=flow_cipher(request)
        )
    return service.flow_run_view(record, flow_cipher(request))


@flow_router.post(
    "/{run_id}/plans", response_model=FlowRunView, operation_id="orchestration.flows.plans.apply"
)
def plan(
    payload: FlowPlanProposal,
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> FlowRunView:
    with session_transaction(session):
        record = service.accept_plan(
            session,
            actor=user,
            run_id=run_id,
            request=payload,
            cipher=flow_cipher(request),
            validate_source=flow_runtime(request).validate_plan_source,
        )
    return service.flow_run_view(record, flow_cipher(request))


@flow_router.post(
    "/{run_id}/plan-reviews",
    response_model=FlowPlanReviewView,
    status_code=201,
    operation_id="orchestration.flows.plans.review",
)
def review_plan(
    payload: FlowPlanReview,
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> FlowPlanReviewView:
    with session_transaction(session):
        record = flow_runtime(request).request_plan_review(
            session, actor=user, run_id=run_id, request=payload
        )
    return FlowPlanReviewView(approval_id=record.id, status=record.status)


@flow_router.get(
    "/{run_id}/steps",
    response_model=list[FlowStepView],
    operation_id="orchestration.flows.steps.list",
)
def steps(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after_path: str = "",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FlowStepView]:
    load_flow_run(session, actor=user, run_id=run_id)
    return [
        FlowStepView(
            path=record.path,
            generation=record.generation,
            status=record.status,
            child_kind=record.child_kind,
            child_id=record.child_id,
            error_code=record.error_code,
        )
        for record in session.scalars(
            select(StepAttempt)
            .where(StepAttempt.run_id == run_id, StepAttempt.path > after_path)
            .order_by(StepAttempt.path)
            .limit(limit)
        )
    ]


@flow_router.get(
    "/{run_id}/events",
    response_model=list[FlowEventView],
    operation_id="orchestration.flows.events.list",
)
def events(
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FlowEventView]:
    load_flow_run(session, actor=user, run_id=run_id)
    return [
        FlowEventView(sequence=record.sequence, kind=record.kind, summary=record.summary)
        for record in session.scalars(
            select(FlowEvent)
            .where(FlowEvent.run_id == run_id, FlowEvent.sequence > after_sequence)
            .order_by(FlowEvent.sequence)
            .limit(limit)
        )
    ]


agent_router = APIRouter(prefix="/orchestration/runs", tags=["orchestration"])
Key = Annotated[str, Query(min_length=1, max_length=200)]


@agent_router.get("", response_model=list[RunView], operation_id="orchestration.runs.list")
def list_runs(
    session_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[RunView]:
    load_visible_session(
        session,
        session_id=session_id,
        subject=ExecutionSubject(user.user_id),
        admission=_scope_admission(request),
    )
    rows = session.scalars(
        select(Run)
        .where(
            Run.session_id == session_id,
            Run.principal_id == user.user_id,
            Run.sequence > after_sequence,
        )
        .order_by(Run.sequence)
        .limit(limit)
    )
    return [service.run_view(row, cipher_of(request)) for row in rows]


@agent_router.post(
    "", response_model=RunView, status_code=202, operation_id="orchestration.runs.create"
)
def create_run(
    payload: ExecutionRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RunView:
    if not isinstance(getattr(request.app.state, "execution_bindings", None), ExecutionBindings):
        raise HTTPException(503, "Execution ledgers are not assembled")
    cipher = cipher_of(request)
    with session_transaction(session):
        sources = request.app.state.execution_bindings.sources
        if sources is not None:
            sources.authorize_submission(
                session, principal_id=user.user_id, configuration=payload.model_dump(mode="json")
            )
        record = service.execute(
            session,
            subject=ExecutionSubject(user.user_id),
            admission=_scope_admission(request),
            cipher=cipher,
            request=payload,
            idempotency_key=idempotency_key,
        )
    return service.run_view(record, cipher)


@agent_router.get("/{run_id}", response_model=RunView, operation_id="orchestration.runs.get")
def get_run(run_id: UUID, user: CurrentUserDep, session: DbSession, request: Request) -> RunView:
    run = load_run(
        session,
        run_id=run_id,
        subject=ExecutionSubject(user.user_id),
        admission=_scope_admission(request),
    )
    return service.run_view(run, cipher_of(request))


@agent_router.post(
    "/{run_id}/cancel", response_model=RunView, operation_id="orchestration.runs.cancel"
)
def cancel_run(
    run_id: UUID, payload: CancelRequest, user: CurrentUserDep, session: DbSession, request: Request
) -> RunView:
    with session_transaction(session):
        run = service.cancel(
            session,
            run_id=run_id,
            subject=ExecutionSubject(user.user_id),
            admission=_scope_admission(request),
            cipher=cipher_of(request),
            request=payload,
        )
    return service.run_view(run, cipher_of(request))


@agent_router.get(
    "/{run_id}/operations",
    response_model=list[OperationView],
    operation_id="orchestration.runs.operations",
)
def operations(
    run_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> list[OperationView]:
    load_run(
        session,
        run_id=run_id,
        subject=ExecutionSubject(user.user_id),
        admission=_scope_admission(request),
    )
    effects = session.scalars(
        select(OperationReceipt)
        .where(OperationReceipt.run_id == run_id)
        .order_by(OperationReceipt.operation_id)
    )
    result = [
        OperationView(
            operation_id=item.operation_id,
            kind=item.kind,
            status=item.status,
            accounted=item.accounted,
            usage=item.usage,
        )
        for item in effects
    ]
    history = session.scalars(
        select(SessionOperationReceipt)
        .where(SessionOperationReceipt.run_id == run_id)
        .order_by(SessionOperationReceipt.operation_id)
    )
    result.extend(
        OperationView(
            operation_id=item.operation_id,
            kind="history",
            status=item.status,
            accounted=True,
            usage={},
        )
        for item in history
    )
    transfers = session.scalars(
        select(EnvironmentOperationReceipt)
        .where(EnvironmentOperationReceipt.run_id == run_id)
        .order_by(EnvironmentOperationReceipt.operation_id)
    )
    result.extend(
        OperationView(
            operation_id=item.operation_id,
            kind="environment",
            status=item.status,
            accounted=item.status == "completed",
            usage={},
        )
        for item in transfers
    )
    return result


@agent_router.post(
    "/{run_id}/resume",
    response_model=RunView,
    status_code=202,
    operation_id="orchestration.runs.resume",
)
def resume_run(
    run_id: UUID, payload: ResumeRequest, user: CurrentUserDep, session: DbSession, request: Request
) -> RunView:
    with session_transaction(session):
        run = reconciliation.resume(
            session,
            run_id=run_id,
            subject=ExecutionSubject(user.user_id),
            admission=_scope_admission(request),
            request=payload,
        )
    return service.run_view(run, cipher_of(request))


@agent_router.post(
    "/{run_id}/reconcile", response_model=RunView, operation_id="orchestration.runs.reconcile"
)
def reconcile_run(
    run_id: UUID,
    payload: ReconcileRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RunView:
    bindings = getattr(request.app.state, "execution_bindings", None)
    if not isinstance(bindings, ExecutionBindings):
        raise HTTPException(503, "Execution ledgers are not assembled")
    with session_transaction(session):
        run = reconciliation.reconcile(
            session,
            run_id=run_id,
            actor=user,
            request=payload,
            cipher=cipher_of(request),
            bindings=bindings,
        )
    return service.run_view(run, cipher_of(request))


@agent_router.get(
    "/{run_id}/events", response_model=EventPage, operation_id="orchestration.runs.events.list"
)
def list_events(
    run_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> EventPage:
    run = load_run(
        session,
        run_id=run_id,
        subject=ExecutionSubject(user.user_id),
        admission=_scope_admission(request),
    )
    return read_events(session, run=run, cipher=cipher_of(request), after=after, limit=limit)


@agent_router.get(
    "/{run_id}/events/stream",
    operation_id="orchestration.runs.events.stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/EventView"}}}
        }
    },
)
async def stream_events(
    run_id: UUID,
    user: CurrentUserDep,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    sessions = request.app.state.database_sessions
    if not isinstance(sessions, sessionmaker):
        raise HTTPException(503, "Execution is not assembled")
    admission = _scope_admission(request)
    subject = ExecutionSubject(user.user_id)
    cipher = cipher_of(request)
    with sessions() as session:
        load_run(session, run_id=run_id, subject=subject, admission=admission)

    async def events() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            with sessions() as session:
                try:
                    authenticated = current_user(
                        session=session,
                        auth_service=get_auth_service(request),
                        resolver=get_delegation_resolver(request),
                        x_session_id=request.headers.get(SESSION_HEADER),
                        x_installation_id=request.headers.get(INSTALLATION_HEADER),
                        x_instance_id=request.headers.get(INSTANCE_HEADER),
                        x_invocation_id=request.headers.get(INVOCATION_HEADER),
                        x_namespace=request.headers.get(NAMESPACE_HEADER),
                    )
                    if authenticated.user_id != subject.user_id:
                        return
                except DomainError:
                    return
                run = load_run(session, run_id=run_id, subject=subject, admission=admission)
                page = read_events(session, run=run, cipher=cipher, after=cursor, limit=100)
                terminal = run.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "timed_out",
                    "needs_attention",
                }
            for event in page.events:
                yield f"id: {event.sequence}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
            cursor = page.next_cursor
            if terminal and not page.events:
                return
            if not page.events:
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


router = APIRouter()
router.include_router(agent_router)
router.include_router(flow_router)
