"""Durable native operations, executed only by the connection-owning worker."""

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.gateway.adapters.base import Adapter
from opsmesh.gateway.adapters.errors import OperationRejected
from opsmesh.infra.database import now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.users.service import identity, require_admin

from .models import ChannelConfiguration, ChannelOperation

logger = logging.getLogger(__name__)
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


def bounded_json(value: Any) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    except (ValueError, TypeError) as exc:
        raise InvalidArgumentError("Channel payload must be finite JSON") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise InvalidArgumentError("Channel payload exceeds 8 MiB")


def enqueue(
    db: Session,
    cipher: SecretCipher,
    channel_id: str,
    owner_id: str,
    operation: str,
    parameters: dict[str, Any],
    key: str,
) -> ChannelOperation:
    channel = db.get(ChannelConfiguration, channel_id, with_for_update=True)
    if channel is None:
        raise NotFoundError("Channel not found")
    if not channel.enabled:
        raise ConflictError("Channel is disabled")
    if not operation or len(operation) > 100 or not key or len(key) > 100:
        raise InvalidArgumentError("Operation and idempotency key must contain 1 to 100 characters")
    bounded_json(parameters)
    existing = db.scalar(
        select(ChannelOperation).where(
            ChannelOperation.channel_id == channel_id, ChannelOperation.idempotency_key == key
        )
    )
    if existing is not None:
        if (
            existing.owner_id != owner_id
            or existing.operation != operation
            or cipher.decrypt(existing.encrypted_parameters) != parameters
        ):
            raise ConflictError("Idempotency key was used for another operation")
        return existing
    if operation not in channel.capabilities.get("operations", {}):
        raise InvalidArgumentError("Operation is not declared by the channel catalogue")
    row = ChannelOperation(
        channel_id=channel_id,
        owner_id=owner_id,
        operation=operation,
        idempotency_key=key,
        encrypted_parameters=cipher.encrypt(parameters),
    )
    db.add(row)
    db.flush()
    return row


async def execute_pending(
    sessions: sessionmaker,
    cipher: SecretCipher,
    channel_id: str,
    adapter: Adapter,
    worker_id: str,
) -> None:
    while True:
        await adapter.ready.wait()
        with sessions.begin() as db:
            channel = db.get(ChannelConfiguration, channel_id)
            if (
                channel is None
                or not channel.enabled
                or channel.worker_id != worker_id
                or channel.lease_until is None
                or channel.lease_until <= now()
            ):
                return
            row = db.scalar(
                select(ChannelOperation)
                .where(
                    ChannelOperation.channel_id == channel_id, ChannelOperation.status == "pending"
                )
                .order_by(ChannelOperation.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                operation_id, operation, parameters = None, "", {}
            else:
                try:
                    require_admin(identity(db, row.owner_id))
                except ForbiddenError:
                    row.status, row.error_code, row.finished_at = (
                        "failed",
                        "authorization_revoked",
                        now(),
                    )
                    continue
                if row.operation not in adapter.capabilities.operations:
                    row.status, row.error_code, row.finished_at = (
                        "failed",
                        "unknown_channel_operation",
                        now(),
                    )
                    continue
                row.status, row.attempts = "running", row.attempts + 1
                operation_id, operation, parameters = (
                    row.id,
                    row.operation,
                    cipher.decrypt(row.encrypted_parameters),
                )
        if operation_id is None:
            await asyncio.sleep(0.5)
            continue
        result, error = None, None
        try:
            async with asyncio.timeout(60):
                result = (await adapter.call(operation, parameters)).model_dump(mode="json")
            bounded_json(result)
            state = "succeeded"
        except asyncio.CancelledError:
            with sessions.begin() as db:
                row = db.get(ChannelOperation, operation_id, with_for_update=True)
                if row is not None and row.status == "running":
                    row.status, row.error_code, row.finished_at = (
                        "uncertain",
                        "operation_cancelled",
                        now(),
                    )
            raise
        except OperationRejected as exc:
            state, error = "failed", exc.code
        except Exception as exc:
            state, error = "uncertain", type(exc).__name__
            logger.warning("gateway_operation_uncertain")
        with sessions.begin() as db:
            row = db.get(ChannelOperation, operation_id, with_for_update=True)
            if row is not None and row.status == "running":
                row.status, row.error_code, row.finished_at = state, error, now()
                if state == "succeeded":
                    row.encrypted_result = cipher.encrypt({"value": result})
