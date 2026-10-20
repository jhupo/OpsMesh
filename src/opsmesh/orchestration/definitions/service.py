"""Composition authoring, explicit sharing and immutable publication."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec
from opsmesh.orchestration.definitions.graph import validate_definition
from opsmesh.orchestration.definitions.models import Definition, DefinitionGrant, DefinitionRevision
from opsmesh.orchestration.definitions.schemas import (
    AgentGraphSpec,
    DefinitionCreate,
    DefinitionUpdate,
    Permission,
)
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.redaction import (
    RedactionRules,
    redact_sensitive_payload,
    redaction_rules_for,
)
from opsmesh.shared.serialization import payload_digest


def granted(permissions: list[str], permission: Permission) -> bool:
    return permission in permissions or "manage" in permissions


def can_view_draft(session: Session, *, actor: CurrentUser, record: Definition) -> bool:
    """Only the owner or an editor may read mutable composition state."""
    if record.owner_id == actor.user_id:
        return True
    grant = session.scalar(
        select(DefinitionGrant).where(
            DefinitionGrant.definition_id == record.id,
            DefinitionGrant.user_id == actor.user_id,
        )
    )
    return grant is not None and granted(grant.permissions, "edit")


def load_definition(
    session: Session,
    *,
    actor: CurrentUser,
    definition_id: UUID,
    manage: bool = False,
    lock: bool = False,
    permission: Permission = "read",
) -> Definition:
    if actor.status != "active":
        raise ForbiddenError("The composition principal is inactive")
    record = session.get(Definition, definition_id, populate_existing=True, with_for_update=lock)
    if record is None:
        raise NotFoundError("Composition definition not found")
    requested: Permission = "manage" if manage else permission
    if record.owner_id != actor.user_id:
        grant = session.scalar(
            select(DefinitionGrant).where(
                DefinitionGrant.definition_id == definition_id,
                DefinitionGrant.user_id == actor.user_id,
            )
        )
        if not (
            (
                requested in {"read", "use"}
                and record.visibility == "public"
                and record.published_number > 0
            )
            or (grant is not None and granted(grant.permissions, requested))
        ):
            raise NotFoundError("Composition definition not found")
    return record


def parse_spec(record: Definition, value: dict[str, object]) -> AgentGraphSpec | FlowDefinitionSpec:
    if record.kind == "agent_graph":
        return AgentGraphSpec.model_validate(value)
    return FlowDefinitionSpec.model_validate(value)


def validate_spec(spec: AgentGraphSpec | FlowDefinitionSpec, *, rules: RedactionRules) -> None:
    if isinstance(spec, FlowDefinitionSpec):
        validate_definition(spec)
    payload = spec.model_dump(mode="json")
    if redact_sensitive_payload(payload, rules=rules) != payload:
        raise InvalidArgumentError("Composition definitions cannot contain literal credentials")


def create_definition(
    session: Session,
    *,
    actor: CurrentUser,
    request: DefinitionCreate,
) -> Definition:
    if actor.status != "active":
        raise ForbiddenError("The composition principal is inactive")
    validate_spec(request.spec, rules=redaction_rules_for(session))
    record = Definition(
        owner_id=actor.user_id,
        name=request.name,
        kind=request.kind,
        visibility=request.visibility,
        draft=request.spec.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return record


def update_definition(
    session: Session,
    *,
    actor: CurrentUser,
    definition_id: UUID,
    request: DefinitionUpdate,
) -> Definition:
    record = load_definition(
        session, actor=actor, definition_id=definition_id, permission="edit", lock=True
    )
    if record.revision != request.expected_revision or record.status != "active":
        raise ConflictError("The composition definition revision or state changed")
    if (record.kind == "agent_graph") != isinstance(request.spec, AgentGraphSpec):
        raise InvalidArgumentError("Definition kind cannot change")
    if record.visibility != request.visibility:
        load_definition(session, actor=actor, definition_id=definition_id, manage=True)
    validate_spec(request.spec, rules=redaction_rules_for(session))
    record.name, record.draft = request.name, request.spec.model_dump(mode="json")
    record.visibility = request.visibility
    record.revision += 1
    return record


def publish_definition(
    session: Session,
    *,
    actor: CurrentUser,
    definition_id: UUID,
    expected_revision: int,
) -> DefinitionRevision:
    record = load_definition(
        session, actor=actor, definition_id=definition_id, manage=True, lock=True
    )
    if record.revision != expected_revision or record.status != "active":
        raise ConflictError("The composition definition revision or state changed")
    spec = parse_spec(record, record.draft)
    validate_spec(spec, rules=redaction_rules_for(session))
    record.published_number += 1
    record.revision += 1
    payload = spec.model_dump(mode="json")
    version = DefinitionRevision(
        definition_id=record.id,
        number=record.published_number,
        spec=payload,
        digest=payload_digest(payload),
        published_by=actor.user_id,
    )
    session.add(version)
    session.flush()
    return version


def load_revision(
    session: Session,
    *,
    actor: CurrentUser,
    revision_id: UUID,
    use: bool = False,
) -> tuple[DefinitionRevision, Definition]:
    record = session.get(DefinitionRevision, revision_id)
    if record is None:
        raise NotFoundError("Composition revision not found")
    definition = load_definition(
        session,
        actor=actor,
        definition_id=record.definition_id,
        permission="use" if use else "read",
    )
    if use and definition.status != "active":
        raise ConflictError("The composition definition is archived")
    return record, definition
