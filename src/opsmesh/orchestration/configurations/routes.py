"""Shared configuration access with private participant conversations and Runs."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import or_, select

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.orchestration.configurations import service as configurations
from opsmesh.orchestration.configurations.closing import close_configuration
from opsmesh.orchestration.configurations.file_routes import router as files_router
from opsmesh.orchestration.configurations.models import (
    ConfigurationClosure,
    ConfigurationInvitation,
    ConfigurationMember,
    ConfigurationRevision,
    UseConfiguration,
)
from opsmesh.orchestration.configurations.schemas import (
    ConfigurationCloseRequest,
    ConfigurationClosureView,
    ConfigurationCreate,
    ConfigurationInvitationCreate,
    ConfigurationInvitationView,
    ConfigurationMembershipView,
    ConfigurationRevisionView,
    ConfigurationRunCreate,
    ConfigurationUpdate,
    ConfigurationView,
)
from opsmesh.orchestration.configurations.service import (
    ConfigurationResources,
    authorize_configuration,
    authorize_configuration_session,
    default_configuration_session,
    submit_configuration_run,
)
from opsmesh.orchestration.contracts import ExecutionSubject
from opsmesh.orchestration.dependencies import flow_cipher, flow_runtime
from opsmesh.orchestration.runs import service as runs
from opsmesh.orchestration.runs.events import read_events
from opsmesh.orchestration.runs.models import Run
from opsmesh.orchestration.runs.schemas import CancelRequest, EventPage, RunView
from opsmesh.orchestration.sessions.history import read_items
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.orchestration.sessions.schemas import HistoryPage, SessionView
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError

router = APIRouter(prefix="/orchestration/configurations", tags=["orchestration"])


@router.post(
    "/{configuration_id}/close",
    status_code=202,
    response_model=ConfigurationClosureView,
    operation_id="orchestration.configurations.close",
)
def close(
    configuration_id: UUID,
    payload: ConfigurationCloseRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> ConfigurationClosureView:
    with session_transaction(session):
        record = close_configuration(
            session,
            actor=user,
            configuration_id=configuration_id,
            expected_revision=payload.expected_revision,
        )
    return ConfigurationClosureView(
        id=record.id,
        configuration_id=record.configuration_id,
        status=record.status,
        detail=record.detail,
    )


@router.get(
    "/{configuration_id}/close",
    response_model=ConfigurationClosureView,
    operation_id="orchestration.configurations.get_closure",
)
def closure_status(
    configuration_id: UUID, session: DbSession, user: CurrentUserDep
) -> ConfigurationClosureView:
    authorize_configuration(
        session, actor=user, configuration_id=configuration_id, allow_disabled=True
    )
    record = session.scalar(
        select(ConfigurationClosure).where(
            ConfigurationClosure.configuration_id == configuration_id
        )
    )
    if record is None:
        raise NotFoundError("The configuration closure is unavailable")
    return ConfigurationClosureView(
        id=record.id,
        configuration_id=record.configuration_id,
        status=record.status,
        detail=record.detail,
    )


def view(record: UseConfiguration) -> ConfigurationView:
    return ConfigurationView(
        id=record.id,
        owner_user_id=record.owner_user_id,
        definition_revision_id=record.definition_revision_id,
        root_directory_id=record.root_directory_id,
        name=record.name,
        status=record.status,
        revision=record.revision,
    )


def revision_view(record: ConfigurationRevision) -> ConfigurationRevisionView:
    return ConfigurationRevisionView(
        id=record.id,
        configuration_id=record.configuration_id,
        number=record.number,
        specification=record.specification,
        digest=record.digest,
    )


def session_view(record: SessionRecord) -> SessionView:
    return SessionView(
        session_id=record.id,
        configuration_id=record.configuration_id,
        scope_id=record.scope_id,
        principal_id=record.principal_id,
        owner_ref=record.owner_ref,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def configuration_resources(request: Request) -> ConfigurationResources:
    resources = getattr(request.app.state, "configuration_resources", None)
    if resources is None:
        raise HTTPException(503, "Configuration resource authorization is not assembled")
    return cast(ConfigurationResources, resources)


@router.post(
    "",
    response_model=ConfigurationView,
    status_code=201,
    operation_id="orchestration.configurations.create",
)
def create(
    payload: ConfigurationCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: str = Query(min_length=1, max_length=150),
) -> ConfigurationView:
    with session_transaction(session):
        record = configurations.create_configuration(
            session,
            actor=user,
            request=payload,
            resources=configuration_resources(request),
            idempotency_key=idempotency_key,
            admission=flow_runtime(request).admission,
        )
    return view(record)


@router.get(
    "", response_model=list[ConfigurationView], operation_id="orchestration.configurations.list"
)
def list_configurations(
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ConfigurationView]:
    memberships = select(ConfigurationMember.configuration_id).where(
        ConfigurationMember.user_id == user.user_id
    )
    query = select(UseConfiguration).where(
        or_(UseConfiguration.owner_user_id == user.user_id, UseConfiguration.id.in_(memberships))
    )
    if after:
        query = query.where(UseConfiguration.id > after)
    return [view(row) for row in session.scalars(query.order_by(UseConfiguration.id).limit(limit))]


@router.get(
    "/{configuration_id}",
    response_model=ConfigurationView,
    operation_id="orchestration.configurations.get",
)
def get(configuration_id: UUID, session: DbSession, user: CurrentUserDep) -> ConfigurationView:
    return view(
        authorize_configuration(
            session, actor=user, configuration_id=configuration_id, allow_disabled=True
        )
    )


@router.put(
    "/{configuration_id}",
    response_model=ConfigurationRevisionView,
    operation_id="orchestration.configurations.update",
)
def update(
    configuration_id: UUID,
    payload: ConfigurationUpdate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> ConfigurationRevisionView:
    with session_transaction(session):
        record = configurations.update_configuration(
            session,
            actor=user,
            configuration_id=configuration_id,
            request=payload,
            admission=flow_runtime(request).admission,
        )
    return revision_view(record)


@router.get(
    "/{configuration_id}/revisions",
    response_model=list[ConfigurationRevisionView],
    operation_id="orchestration.configurations.revisions.list",
)
def revisions(
    configuration_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after_number: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ConfigurationRevisionView]:
    authorize_configuration(session, actor=user, configuration_id=configuration_id)
    return [
        revision_view(row)
        for row in session.scalars(
            select(ConfigurationRevision)
            .where(
                ConfigurationRevision.configuration_id == configuration_id,
                ConfigurationRevision.number > after_number,
            )
            .order_by(ConfigurationRevision.number)
            .limit(limit)
        )
    ]


@router.post(
    "/{configuration_id}/invitations",
    status_code=201,
    response_model=ConfigurationInvitationView,
    operation_id="orchestration.configurations.invitations.create",
)
def invite(
    configuration_id: UUID,
    payload: ConfigurationInvitationCreate,
    session: DbSession,
    user: CurrentUserDep,
) -> ConfigurationInvitationView:
    with session_transaction(session):
        record = configurations.invite_member(
            session, actor=user, configuration_id=configuration_id, request=payload
        )
    return ConfigurationInvitationView(
        id=record.id, configuration_id=record.configuration_id, status=record.status
    )


@router.post(
    "/invitations/{invitation_id}/accept",
    status_code=200,
    response_model=ConfigurationMembershipView,
    operation_id="orchestration.configurations.invitations.accept",
)
def accept(
    invitation_id: UUID, session: DbSession, user: CurrentUserDep
) -> ConfigurationMembershipView:
    with session_transaction(session):
        record = configurations.accept_invitation(session, actor=user, invitation_id=invitation_id)
    return ConfigurationMembershipView(configuration_id=record.configuration_id)


@router.get(
    "/invitations/pending",
    response_model=list[ConfigurationInvitationView],
    operation_id="orchestration.configurations.invitations.list",
)
def pending_invitations(
    session: DbSession, user: CurrentUserDep
) -> list[ConfigurationInvitationView]:
    return [
        ConfigurationInvitationView(
            id=row.id, configuration_id=row.configuration_id, status=row.status
        )
        for row in session.scalars(
            select(ConfigurationInvitation)
            .where(
                ConfigurationInvitation.invited_user_id == user.user_id,
                ConfigurationInvitation.status == "pending",
            )
            .order_by(ConfigurationInvitation.id)
            .limit(500)
        )
    ]


@router.delete(
    "/{configuration_id}/members/{user_id}",
    status_code=204,
    operation_id="orchestration.configurations.members.delete",
)
def remove_member(
    configuration_id: UUID,
    user_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
) -> None:
    with session_transaction(session):
        authorize_configuration(
            session, actor=user, configuration_id=configuration_id, permission="manage", lock=True
        )
        member = session.scalar(
            select(ConfigurationMember).where(
                ConfigurationMember.configuration_id == configuration_id,
                ConfigurationMember.user_id == user_id,
            )
        )
        if member:
            session.delete(member)
        for invitation in session.scalars(
            select(ConfigurationInvitation).where(
                ConfigurationInvitation.configuration_id == configuration_id,
                ConfigurationInvitation.invited_user_id == user_id,
                ConfigurationInvitation.status == "pending",
            )
        ):
            invitation.status = "revoked"


@router.post(
    "/{configuration_id}/sessions",
    response_model=SessionView,
    status_code=201,
    operation_id="orchestration.configurations.sessions.create",
)
def create_session(
    configuration_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> SessionView:
    with session_transaction(session):
        record = default_configuration_session(
            session,
            actor=user,
            configuration_id=configuration_id,
            admission=flow_runtime(request).admission,
        )
    return session_view(record)


@router.get(
    "/{configuration_id}/sessions",
    response_model=list[SessionView],
    operation_id="orchestration.configurations.sessions.list",
)
def list_sessions(
    configuration_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SessionView]:
    authorize_configuration(session, actor=user, configuration_id=configuration_id)
    query = select(SessionRecord).where(
        SessionRecord.configuration_id == configuration_id,
        SessionRecord.scope_id == user.user_id,
        SessionRecord.owner_ref == f"user:{user.user_id}",
        SessionRecord.principal_id == user.user_id,
    )
    if after:
        query = query.where(SessionRecord.id > after)
    return [
        session_view(row) for row in session.scalars(query.order_by(SessionRecord.id).limit(limit))
    ]


@router.get(
    "/{configuration_id}/sessions/{session_id}/history",
    response_model=HistoryPage,
    operation_id="orchestration.configurations.sessions.history",
)
def history(
    configuration_id: UUID,
    session_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> HistoryPage:
    authorize_configuration_session(
        session, actor=user, configuration_id=configuration_id, session_id=session_id
    )
    return HistoryPage(items=read_items(session, session_id=session_id, limit=limit))


@router.post(
    "/{configuration_id}/runs",
    response_model=RunView,
    status_code=202,
    operation_id="orchestration.configurations.runs.create",
)
def run(
    configuration_id: UUID,
    payload: ConfigurationRunCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: str = Query(min_length=1, max_length=150),
) -> RunView:
    runtime = flow_runtime(request)
    with session_transaction(session):
        record = submit_configuration_run(
            session,
            actor=user,
            configuration_id=configuration_id,
            request=payload,
            idempotency_key=idempotency_key,
            admission=runtime.admission,
            cipher=runtime.cipher,
            assets=runtime.assets,
        )
    return runs.run_view(record, runtime.cipher)


def configuration_run(
    session: DbSession, user: CurrentUserDep, configuration_id: UUID, run_id: UUID
) -> Run:
    record = session.get(Run, run_id)
    if (
        record is None
        or record.principal_id != user.user_id
        or record.owner_ref != f"configuration:{configuration_id}"
    ):
        raise NotFoundError("Configuration Run not found")
    authorize_configuration_session(
        session, actor=user, configuration_id=configuration_id, session_id=record.session_id
    )
    return record


@router.get(
    "/{configuration_id}/runs/{run_id}",
    response_model=RunView,
    operation_id="orchestration.configurations.runs.get",
)
def get_run(
    configuration_id: UUID,
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> RunView:
    return runs.run_view(
        configuration_run(session, user, configuration_id, run_id), flow_cipher(request)
    )


@router.get(
    "/{configuration_id}/runs/{run_id}/events",
    response_model=EventPage,
    operation_id="orchestration.configurations.runs.events",
)
def events(
    configuration_id: UUID,
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> EventPage:
    record = configuration_run(session, user, configuration_id, run_id)
    return read_events(
        session, run=record, cipher=flow_cipher(request), after=after_sequence, limit=limit
    )


@router.post(
    "/{configuration_id}/runs/{run_id}/cancel",
    response_model=RunView,
    operation_id="orchestration.configurations.runs.cancel",
)
def cancel(
    configuration_id: UUID,
    run_id: UUID,
    payload: CancelRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> RunView:
    with session_transaction(session):
        authorize_configuration(
            session, actor=user, configuration_id=configuration_id, permission="use"
        )
        record = configuration_run(session, user, configuration_id, run_id)
        runtime = flow_runtime(request)
        record = runs.cancel(
            session,
            subject=ExecutionSubject(user.user_id),
            admission=runtime.admission,
            run_id=record.id,
            cipher=runtime.cipher,
            request=payload,
        )
    return runs.run_view(record, flow_cipher(request))


router.include_router(files_router)
