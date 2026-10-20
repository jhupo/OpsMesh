"""Flow authoring, validation, immutable publication and definition authorization."""

from collections.abc import Callable
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.definitions import service
from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec, FlowNode
from opsmesh.orchestration.definitions.models import Definition, DefinitionGrant, DefinitionRevision
from opsmesh.orchestration.definitions.schemas import (
    DefinitionCreate,
    DefinitionGrantRequest,
    DefinitionRevisionView,
    DefinitionUpdate,
    DefinitionView,
    FlowAuthoringContract,
    FlowPublishRequest,
    FlowValidationResult,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.redaction import redaction_rules_for

router = APIRouter(prefix="/orchestration/definitions", tags=["orchestration"])
DefinitionValidator = Callable[[Session, CurrentUser, FlowDefinitionSpec], None]


def definition_view(
    record: Definition, *, session: Session, user: CurrentUser
) -> DefinitionView:
    include_draft = service.can_view_draft(session, actor=user, record=record)
    return DefinitionView(
        id=record.id,
        name=record.name,
        owner_id=record.owner_id,
        kind=record.kind,
        visibility=record.visibility,
        status=record.status,
        revision=record.revision,
        draft=service.parse_spec(record, record.draft) if include_draft else None,
    )


def revision_view(record: DefinitionRevision) -> DefinitionRevisionView:
    return DefinitionRevisionView(
        id=record.id,
        definition_id=record.definition_id,
        number=record.number,
        digest=record.digest,
        spec=record.spec,
    )


@router.get(
    "/authoring-contract",
    response_model=FlowAuthoringContract,
    operation_id="orchestration.definitions.authoring_contract",
)
def authoring_contract(user: CurrentUserDep) -> FlowAuthoringContract:
    return FlowAuthoringContract(
        node_kinds=[
            "agent",
            "operation",
            "condition",
            "parallel",
            "join",
            "foreach",
            "repeat",
            "subflow",
            "wait",
            "expand",
        ],
        mapping_roots=["input", "steps", "item", "index"],
        max_nodes=256,
        max_nested_depth=8,
        definition_schema=FlowNode.model_json_schema(),
    )


@router.post(
    "",
    response_model=DefinitionView,
    status_code=201,
    operation_id="orchestration.definitions.create",
)
def create(payload: DefinitionCreate, session: DbSession, user: CurrentUserDep) -> DefinitionView:
    with session_transaction(session):
        record = service.create_definition(session, actor=user, request=payload)
    return definition_view(record, session=session, user=user)


@router.get("", response_model=list[DefinitionView], operation_id="orchestration.definitions.list")
def list_definitions(
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DefinitionView]:
    visible_grants = select(DefinitionGrant.definition_id).where(
        DefinitionGrant.user_id == user.user_id,
    )
    query = select(Definition).where(
        or_(
            Definition.owner_id == user.user_id,
            (Definition.visibility == "public") & (Definition.published_number > 0),
            Definition.id.in_(visible_grants),
        )
    )
    if after:
        query = query.where(Definition.id > after)
    return [
        definition_view(record, session=session, user=user)
        for record in session.scalars(query.order_by(Definition.id).limit(limit))
    ]


@router.get(
    "/{definition_id}",
    response_model=DefinitionView,
    operation_id="orchestration.definitions.get",
)
def get(definition_id: UUID, session: DbSession, user: CurrentUserDep) -> DefinitionView:
    return definition_view(
        service.load_definition(session, actor=user, definition_id=definition_id),
        session=session,
        user=user,
    )


@router.put(
    "/{definition_id}",
    response_model=DefinitionView,
    operation_id="orchestration.definitions.update",
)
def update(
    payload: DefinitionUpdate, definition_id: UUID, session: DbSession, user: CurrentUserDep
) -> DefinitionView:
    with session_transaction(session):
        record = service.update_definition(
            session, actor=user, definition_id=definition_id, request=payload
        )
    return definition_view(record, session=session, user=user)


@router.post(
    "/{definition_id}/validate",
    response_model=FlowValidationResult,
    operation_id="orchestration.definitions.validate",
)
def validate(
    definition_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> FlowValidationResult:
    record = service.load_definition(session, actor=user, definition_id=definition_id, manage=True)
    spec = service.parse_spec(record, record.draft)
    service.validate_spec(spec, rules=redaction_rules_for(session))
    if isinstance(spec, FlowDefinitionSpec):
        validator = getattr(request.app.state, "flow_definition_validator", None)
        if not callable(validator):
            raise HTTPException(503, "Flow reference validation is not assembled")
        cast(DefinitionValidator, validator)(session, user, spec)
    return FlowValidationResult(valid=True, revision=record.revision)


@router.post(
    "/{definition_id}/publish",
    response_model=DefinitionRevisionView,
    operation_id="orchestration.definitions.publish",
)
def publish(
    payload: FlowPublishRequest,
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> DefinitionRevisionView:
    with session_transaction(session):
        validate(definition_id, session, user, request)
        record = service.publish_definition(
            session,
            actor=user,
            definition_id=definition_id,
            expected_revision=payload.expected_revision,
        )
    return revision_view(record)


@router.post(
    "/{definition_id}/archive",
    response_model=DefinitionView,
    operation_id="orchestration.definitions.archive",
)
def archive(
    payload: FlowPublishRequest, definition_id: UUID, session: DbSession, user: CurrentUserDep
) -> DefinitionView:
    with session_transaction(session):
        record = service.load_definition(
            session, actor=user, definition_id=definition_id, manage=True, lock=True
        )
        if record.revision != payload.expected_revision:
            raise ConflictError("The definition revision changed")
        record.status, record.revision = "archived", record.revision + 1
    return definition_view(record, session=session, user=user)


@router.get(
    "/{definition_id}/revisions",
    response_model=list[DefinitionRevisionView],
    operation_id="orchestration.definitions.revisions.list",
)
def revisions(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after_number: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DefinitionRevisionView]:
    service.load_definition(session, actor=user, definition_id=definition_id)
    return [
        revision_view(record)
        for record in session.scalars(
            select(DefinitionRevision)
            .where(
                DefinitionRevision.definition_id == definition_id,
                DefinitionRevision.number > after_number,
            )
            .order_by(DefinitionRevision.number)
            .limit(limit)
        )
    ]


@router.get(
    "/{definition_id}/revisions/{revision_id}",
    response_model=DefinitionRevisionView,
    operation_id="orchestration.definitions.revisions.get",
)
def revision(
    definition_id: UUID, revision_id: UUID, session: DbSession, user: CurrentUserDep
) -> DefinitionRevisionView:
    record, definition = service.load_revision(session, actor=user, revision_id=revision_id)
    if definition.id != definition_id:
        raise ConflictError("The revision belongs to another definition")
    return revision_view(record)


@router.put(
    "/{definition_id}/grants", status_code=204, operation_id="orchestration.definitions.grants.put"
)
def put_grant(
    definition_id: UUID,
    payload: DefinitionGrantRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> None:
    from opsmesh.identity.users.models import UserProfile
    from opsmesh.shared.errors import NotFoundError

    with session_transaction(session):
        service.load_definition(
            session, actor=user, definition_id=definition_id, manage=True, lock=True
        )
        target = session.get(UserProfile, payload.user_id)
        if target is None or target.status != "active":
            raise NotFoundError("The target account is unavailable")
        record = session.scalar(
            select(DefinitionGrant).where(
                DefinitionGrant.definition_id == definition_id,
                DefinitionGrant.user_id == payload.user_id,
            )
        )
        if record is None:
            record = DefinitionGrant(definition_id=definition_id, user_id=payload.user_id)
            session.add(record)
        record.permissions = list(dict.fromkeys(payload.permissions))


@router.delete(
    "/{definition_id}/grants/{user_id}",
    status_code=204,
    operation_id="orchestration.definitions.grants.delete",
)
def delete_grant(
    definition_id: UUID,
    user_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
) -> None:
    with session_transaction(session):
        service.load_definition(
            session, actor=user, definition_id=definition_id, manage=True, lock=True
        )
        record = session.scalar(
            select(DefinitionGrant).where(
                DefinitionGrant.definition_id == definition_id,
                DefinitionGrant.user_id == user_id,
            )
        )
        if record is not None:
            session.delete(record)
