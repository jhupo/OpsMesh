"""Trigger management and signed, replay-protected durable webhook receipts."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.orchestration.dependencies import flow_cipher, flow_runtime
from opsmesh.orchestration.triggers import service
from opsmesh.orchestration.triggers.models import EventSubscription, FlowTrigger
from opsmesh.orchestration.triggers.schemas import (
    TriggerAccepted,
    TriggerConfiguration,
    TriggerControl,
    TriggerCreate,
    TriggerView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError

router = APIRouter(prefix="/orchestration/triggers", tags=["orchestration"])


def trigger_view(record: FlowTrigger) -> TriggerView:
    return TriggerView(
        id=record.id,
        revision_id=record.revision_id,
        configuration_id=record.configuration_id,
        kind=record.kind,
        revision=record.revision,
        status=record.status,
        configuration=TriggerConfiguration.model_validate(record.configuration),
    )


@router.post(
    "", response_model=TriggerView, status_code=201, operation_id="orchestration.triggers.create"
)
def create(
    payload: TriggerCreate, request: Request, session: DbSession, user: CurrentUserDep
) -> TriggerView:
    with session_transaction(session):
        record = service.create_trigger(
            session, actor=user, request=payload, cipher=flow_cipher(request)
        )
        config = payload.configuration
        if payload.kind == "event" and config.instance_id:
            flow_runtime(request).validate_subscription(
                session, actor=user, instance_id=config.instance_id, revision_id=payload.revision_id
            )
    return trigger_view(record)


@router.get("", response_model=list[TriggerView], operation_id="orchestration.triggers.list")
def list_triggers(
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TriggerView]:
    query = select(FlowTrigger).where(FlowTrigger.principal_id == user.user_id)
    if after:
        query = query.where(FlowTrigger.id > after)
    return [
        trigger_view(record)
        for record in session.scalars(query.order_by(FlowTrigger.id).limit(limit))
    ]


@router.get("/{trigger_id}", response_model=TriggerView, operation_id="orchestration.triggers.get")
def get(trigger_id: UUID, session: DbSession, user: CurrentUserDep) -> TriggerView:
    return trigger_view(service.load_trigger(session, actor=user, trigger_id=trigger_id))


@router.put(
    "/{trigger_id}/status",
    response_model=TriggerView,
    operation_id="orchestration.triggers.control",
)
def control(
    trigger_id: UUID, payload: TriggerControl, session: DbSession, user: CurrentUserDep
) -> TriggerView:
    with session_transaction(session):
        record = service.load_trigger(
            session, actor=user, trigger_id=trigger_id, manage=True, lock=True
        )
        if record.revision != payload.expected_revision:
            raise ConflictError("The trigger revision changed")
        record.status, record.revision = payload.status, record.revision + 1
        if record.kind == "event":
            config = TriggerConfiguration.model_validate(record.configuration)
            session.add(
                EventSubscription(
                    trigger_id=record.id,
                    consumer_revision=record.revision,
                    source_kind=config.source_kind,
                    event_type=config.event_type,
                )
            )
        elif record.kind == "calendar":
            service.sync_schedule(session, record)
    return trigger_view(record)


@router.post(
    "/{trigger_id}/webhook",
    response_model=TriggerAccepted,
    status_code=202,
    operation_id="orchestration.triggers.webhook",
)
async def webhook(
    trigger_id: UUID,
    request: Request,
    session: DbSession,
    event_id: str = Header(alias="X-OpsMesh-Event-ID", min_length=1, max_length=200),
    timestamp: int = Header(alias="X-OpsMesh-Timestamp"),
    signature: str = Header(alias="X-OpsMesh-Signature", min_length=64, max_length=64),
) -> TriggerAccepted:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 262144:
            raise InvalidArgumentError("The webhook body exceeds its byte bound")
    with session_transaction(session):
        trigger = session.get(FlowTrigger, trigger_id)
        if (
            trigger is None
            or trigger.kind != "webhook"
            or trigger.status != "enabled"
            or not trigger.secret_ciphertext
        ):
            raise ForbiddenError("The webhook is unavailable")
        config = TriggerConfiguration.model_validate(trigger.configuration)
        if abs(datetime.now(UTC).timestamp() - timestamp) > config.replay_seconds:
            raise ForbiddenError("The webhook timestamp is outside its replay window")
        secret = str(flow_cipher(request).decrypt_payload(trigger.secret_ciphertext)["secret"])
        signed = str(timestamp).encode() + b"." + event_id.encode() + b"." + bytes(raw)
        if not hmac.compare_digest(
            hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest(), signature
        ):
            raise ForbiddenError("Invalid webhook signature")
        try:
            payload = json.loads(
                raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
            )
        except ValueError:
            raise InvalidArgumentError("The webhook body is not JSON") from None
        if not isinstance(payload, dict):
            raise InvalidArgumentError("The webhook body must be an object")
        trigger = service.lock_delivery(
            session, runtime=flow_runtime(request), trigger_id=trigger_id
        )
        receipt = service.deliver(
            session,
            runtime=flow_runtime(request),
            trigger=trigger,
            event_id=f"webhook:{event_id}",
            payload=payload,
        )
    assert receipt.flow_run_id is not None
    return TriggerAccepted(status=receipt.status, flow_run_id=receipt.flow_run_id)
