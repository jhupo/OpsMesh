"""Session-authenticated management and timestamped HMAC webhooks."""

import asyncio
import hashlib
import hmac
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from opsmesh.execution.runtime.routes import secret_cipher
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.serialization import canonical_json
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.workflows.triggers import service
from opsmesh.workflows.triggers.contracts import TriggerSpec
from opsmesh.workflows.triggers.models import Trigger, TriggerDelivery
from opsmesh.workflows.triggers.ports import TargetPorts

router = APIRouter(prefix="/triggers", tags=["triggers"])


class TriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    spec: TriggerSpec
    signing_secret: SecretStr | None = Field(default=None, min_length=32, max_length=4096)


class FireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=200)


def ports(request: Request) -> TargetPorts:
    value = getattr(request.app.state, "automation_targets", None)
    if not isinstance(value, TargetPorts):
        raise HTTPException(503, "Automation execution is not configured")
    return value


def view(row: Trigger, request: Request) -> dict[str, object]:
    return {
        "id": row.id,
        "revision": row.revision,
        "applied_revision": row.applied_revision,
        "status": row.status,
        "sync_error": row.sync_error,
        "spec": service.specification(row, secret_cipher(request)).model_dump(mode="json"),
    }


@router.post("", status_code=201, operation_id="triggers.create")
def create(
    payload: TriggerRequest, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    with session_transaction(session):
        return view(
            service.save(
                session,
                actor=user,
                spec=payload.spec,
                cipher=secret_cipher(request),
                ports=ports(request),
                expected_revision=payload.expected_revision,
                secret=payload.signing_secret.get_secret_value()
                if payload.signing_secret
                else None,
            ),
            request,
        )


@router.get("", operation_id="triggers.list")
def triggers(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    return [
        view(r, request)
        for r in session.scalars(
            select(Trigger)
            .where(Trigger.owner_user_id == user.user_id, Trigger.status != "deleted")
            .order_by(Trigger.created_at.desc(), Trigger.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.put("/{trigger_id}", operation_id="triggers.update")
def update(
    trigger_id: UUID,
    payload: TriggerRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    with session_transaction(session):
        return view(
            service.save(
                session,
                actor=user,
                spec=payload.spec,
                cipher=secret_cipher(request),
                ports=ports(request),
                expected_revision=payload.expected_revision,
                trigger_id=trigger_id,
                secret=payload.signing_secret.get_secret_value()
                if payload.signing_secret
                else None,
            ),
            request,
        )


@router.get("/{trigger_id}", operation_id="triggers.read")
def read(
    trigger_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    return view(service.owned(session, user, trigger_id), request)


@router.delete("/{trigger_id}", status_code=204, operation_id="triggers.delete")
def delete(
    trigger_id: UUID, expected_revision: int, session: DbSession, user: CurrentUserDep
) -> None:
    with session_transaction(session):
        row = service.owned(session, user, trigger_id, lock=True)
        if row.revision != expected_revision:
            raise InvalidArgumentError("The trigger revision changed")
        row.status, row.revision = "deleted", row.revision + 1
        service.audit(session, user, "configuration.delete", row.id)


@router.post("/{trigger_id}/fire", operation_id="triggers.fire")
def fire(
    trigger_id: UUID,
    payload: FireRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    with session_transaction(session):
        row = service.owned(session, user, trigger_id, lock=True)
        receipt = service.fire(
            session,
            row=row,
            event_id=payload.event_id,
            cipher=secret_cipher(request),
            ports=ports(request),
        )
        return {
            "delivery_id": receipt.id,
            "run_id": receipt.run_id,
            "target_kind": receipt.target_kind,
        }


@router.get("/{trigger_id}/deliveries", operation_id="triggers.deliveries.list")
def deliveries(
    trigger_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    service.owned(session, user, trigger_id)
    return [
        {
            "id": r.id,
            "event_id": r.event_id,
            "revision": r.revision,
            "run_id": r.run_id,
            "target_kind": r.target_kind,
            "created_at": r.created_at,
        }
        for r in session.scalars(
            select(TriggerDelivery)
            .where(TriggerDelivery.trigger_id == trigger_id)
            .order_by(TriggerDelivery.created_at.desc(), TriggerDelivery.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.post("/{trigger_id}/webhook", status_code=202, operation_id="triggers.webhook")
async def webhook(trigger_id: UUID, request: Request) -> dict[str, object]:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1_000_000:
            raise HTTPException(413, "The webhook body exceeds its size limit")
    timestamp = request.headers.get("X-OpsMesh-Timestamp", "")
    event_id = request.headers.get("X-OpsMesh-Event-ID", "")
    signature = request.headers.get("X-OpsMesh-Signature", "")
    try:
        if (
            not timestamp.isascii()
            or not timestamp.isdigit()
            or len(timestamp) > 12
            or abs(time.time() - int(timestamp)) > 300
        ):
            raise ValueError
    except (ValueError, OverflowError):
        raise ForbiddenError("The webhook timestamp is invalid or expired") from None
    if not 1 <= len(event_id) <= 200 or any(ord(c) < 32 for c in event_id):
        raise InvalidArgumentError("A webhook requires a bounded event identity")
    try:
        event = json.loads(raw)
        canonical_json({"event": event})
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidArgumentError("The webhook body must be JSON") from None
    cipher, targets = secret_cipher(request), ports(request)

    def deliver() -> dict[str, object]:
        with request.app.state.database_sessions.begin() as session:
            row = session.get(Trigger, trigger_id, with_for_update=True)
            if row is None or row.kind != "webhook" or row.encrypted_secret is None:
                raise NotFoundError("The webhook does not exist")
            secret = cipher.decrypt_payload(row.encrypted_secret).get("secret")
            if not isinstance(secret, str):
                raise ForbiddenError("The signing secret is unavailable")
            signed = timestamp.encode() + b"\n" + event_id.encode() + b"\n" + bytes(raw)
            expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
            if not signature.isascii() or not hmac.compare_digest(expected, signature):
                raise ForbiddenError("The webhook signature is invalid")
            receipt = service.fire(
                session, row=row, event_id=event_id, event=event, cipher=cipher, ports=targets
            )
            return {"delivery_id": receipt.id, "run_id": receipt.run_id}

    return await asyncio.to_thread(deliver)
