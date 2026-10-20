"""Trigger mutations and delivery receipts share the target FlowRun transaction."""

from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.configurations.service import authorize_configuration, owner_identity
from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec
from opsmesh.orchestration.definitions.schema_validation import validate_schema
from opsmesh.orchestration.definitions.service import load_revision
from opsmesh.orchestration.runs.activities import FlowActivities
from opsmesh.orchestration.runs.models import FlowRun
from opsmesh.orchestration.runs.schemas import FlowRunCreate
from opsmesh.orchestration.runs.service import start_run
from opsmesh.orchestration.triggers.models import EventSubscription, FlowTrigger, TriggerReceipt
from opsmesh.orchestration.triggers.schemas import TriggerConfiguration, TriggerCreate, TriggerWork
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.redaction import redact_sensitive_payload, redaction_rules_for
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution


def disable_configuration_triggers(session: Session, *, configuration_id: UUID) -> None:
    for trigger in session.scalars(
        select(FlowTrigger)
        .where(
            FlowTrigger.configuration_id == configuration_id,
            FlowTrigger.status == "enabled",
        )
        .order_by(FlowTrigger.id)
        .with_for_update()
    ):
        trigger.status = "disabled"
        trigger.revision += 1
        if trigger.kind == "calendar":
            sync_schedule(session, trigger)


def reencrypt_webhook_secrets(
    session: Session, *, cipher: SecretCipher, cursor: str | None, limit: int
) -> tuple[int, str | None]:
    query = select(FlowTrigger).where(FlowTrigger.secret_ciphertext.is_not(None))
    if cursor:
        query = query.where(FlowTrigger.id > UUID(cursor))
    rows = list(session.scalars(query.order_by(FlowTrigger.id).limit(limit).with_for_update()))
    for row in rows:
        assert row.secret_ciphertext is not None
        row.secret_ciphertext = cipher.encrypt_payload(
            cipher.decrypt_payload(row.secret_ciphertext)
        ).ciphertext
    return len(rows), str(rows[-1].id) if len(rows) == limit else None


def load_trigger(
    session: Session,
    *,
    actor: CurrentUser,
    trigger_id: UUID,
    manage: bool = False,
    lock: bool = False,
) -> FlowTrigger:
    record = session.get(FlowTrigger, trigger_id, populate_existing=True)
    if record is None or record.principal_id != actor.user_id:
        raise NotFoundError("Trigger not found")
    if record.configuration_id:
        configuration = authorize_configuration(
            session,
            actor=actor,
            configuration_id=record.configuration_id,
            permission="use" if manage else "read",
        )
        load_revision(
            session, actor=owner_identity(session, configuration), revision_id=record.revision_id
        )
    elif actor.user_id != record.principal_id:
        raise ForbiddenError("The trigger belongs to another principal")
    else:
        load_revision(session, actor=actor, revision_id=record.revision_id)
    if lock:
        record = session.get(FlowTrigger, trigger_id, with_for_update=True, populate_existing=True)
        assert record is not None
    return record


def lock_delivery(
    session: Session, *, runtime: FlowActivities, trigger_id: UUID, parent: FlowRun | None = None
) -> FlowTrigger:
    record = session.get(FlowTrigger, trigger_id)
    if record is None:
        raise NotFoundError("Trigger not found")
    actor = runtime.users.load_identity(session, record.initiated_by_id)
    if actor is None or actor.status != "active":
        raise ForbiddenError("The trigger principal is unavailable")
    load_trigger(session, actor=actor, trigger_id=record.id, manage=True)
    if parent:
        session.get(FlowRun, parent.root_id, with_for_update=True, populate_existing=True)
    locked = session.get(FlowTrigger, trigger_id, with_for_update=True, populate_existing=True)
    assert locked is not None
    return locked


def create_trigger(
    session: Session, *, actor: CurrentUser, request: TriggerCreate, cipher: SecretCipher
) -> FlowTrigger:
    configuration = (
        authorize_configuration(
            session, actor=actor, configuration_id=request.configuration_id, permission="use"
        )
        if request.configuration_id
        else None
    )
    principal = actor
    _, definition = load_revision(
        session,
        actor=owner_identity(session, configuration) if configuration else actor,
        revision_id=request.revision_id,
        use=True,
    )
    if definition.kind != "flow" or (
        configuration is not None and configuration.definition_revision_id != request.revision_id
    ):
        raise InvalidArgumentError("The trigger requires the fixed flow configuration revision")
    config = request.configuration.model_dump(mode="json")
    validate_schema(request.configuration.payload_schema)
    try:
        Draft202012Validator.check_schema(request.configuration.payload_schema)
    except Exception:
        raise InvalidArgumentError("Invalid trigger payload schema") from None
    if (
        len(canonical_json(config).encode()) > 65536
        or redact_sensitive_payload(config, rules=redaction_rules_for(session)) != config
    ):
        raise InvalidArgumentError(
            "Trigger configuration exceeds its bound or contains credentials"
        )
    record = FlowTrigger(
        principal_id=principal.user_id,
        initiated_by_id=actor.user_id,
        configuration_id=request.configuration_id,
        revision_id=request.revision_id,
        kind=request.kind,
        configuration=config,
        secret_ciphertext=cipher.encrypt_payload({"secret": request.webhook_secret}).ciphertext
        if request.webhook_secret
        else None,
    )
    session.add(record)
    session.flush()
    if request.kind == "event":
        session.add(
            EventSubscription(
                trigger_id=record.id,
                consumer_revision=record.revision,
                source_kind=request.configuration.source_kind,
                event_type=request.configuration.event_type,
            )
        )
    if request.kind == "calendar":
        sync_schedule(session, record)
    return record


def sync_schedule(session: Session, record: FlowTrigger) -> None:
    identifier = uuid4()
    stage_execution(
        session,
        TriggerWork(
            execution_id=identifier,
            workflow_id=f"trigger-sync:{record.id}:{record.revision}",
            trigger_id=record.id,
            revision=record.revision,
        ),
    )


def deliver(
    session: Session,
    *,
    runtime: FlowActivities,
    trigger: FlowTrigger,
    event_id: str,
    payload: dict[str, Any],
    parent: FlowRun | None = None,
) -> TriggerReceipt:
    fingerprint = payload_digest(payload)
    previous = session.scalar(
        select(TriggerReceipt).where(
            TriggerReceipt.trigger_id == trigger.id,
            TriggerReceipt.consumer_revision == trigger.revision,
            TriggerReceipt.event_id == event_id,
        )
    )
    if previous:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The trigger event identity was reused with different content")
        return previous
    actor = runtime.users.load_identity(session, trigger.initiated_by_id)
    if actor is None or actor.status != "active" or trigger.status != "enabled":
        raise ForbiddenError("The trigger principal or definition is disabled")
    config = TriggerConfiguration.model_validate(trigger.configuration)
    if len(canonical_json(payload).encode()) > 262144 or list(
        Draft202012Validator(config.payload_schema).iter_errors(payload)
    ):
        raise InvalidArgumentError("The trigger payload violates its fixed schema or byte bound")
    load_trigger(session, actor=actor, trigger_id=trigger.id, manage=True)
    configuration = (
        authorize_configuration(
            session, actor=actor, configuration_id=trigger.configuration_id, permission="use"
        )
        if trigger.configuration_id
        else None
    )
    revision, _ = load_revision(
        session,
        actor=owner_identity(session, configuration) if configuration else actor,
        revision_id=trigger.revision_id,
        use=True,
    )
    spec = FlowDefinitionSpec.model_validate(revision.spec)
    effective = {**config.input, "event": payload}
    if list(Draft202012Validator(spec.input_schema).iter_errors(effective)):
        raise InvalidArgumentError("The trigger input violates the fixed flow revision schema")
    run = start_run(
        session,
        actor=actor,
        request=FlowRunCreate(
            revision_id=revision.id, configuration_id=trigger.configuration_id, input=effective
        ),
        idempotency_key=(
            f"trigger:{trigger.id}:{trigger.revision}:{payload_digest({'event_id': event_id})}"
        ),
        cipher=runtime.cipher,
        parent=parent,
    )
    runtime.validate_start(session, actor=actor, run=run)
    receipt = TriggerReceipt(
        trigger_id=trigger.id,
        consumer_revision=trigger.revision,
        event_id=event_id,
        fingerprint=fingerprint,
        flow_run_id=run.id,
        status="accepted",
    )
    session.add(receipt)
    session.flush()
    return receipt
