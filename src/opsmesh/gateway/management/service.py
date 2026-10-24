"""Transactional admission and recovery for all management entry points."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from opsmesh_sdk.channel_management import (
    MANAGEMENT_REQUEST,
    READ_ACTIONS,
    ManagementRequest,
    ManagementResult,
    OperationReceipt,
    ReviewJoinRequest,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.gateway.management.authorization import authorize_operation
from opsmesh.gateway.management.contracts import OperationOrigin
from opsmesh.gateway.storage.models import GatewayChannel, GatewayManagementOperation
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.serialization import payload_digest


class Management:
    def __init__(self, store: GatewayStore, adapter_generation: Callable[[str], str]) -> None:
        self.store, self.adapter_generation = store, adapter_generation

    def receipt(self, row: GatewayManagementOperation) -> OperationReceipt:
        return OperationReceipt.model_validate(
            {
                "id": row.id,
                "operation": self.request(row),
                "actor_id": row.actor_id,
                "resource_id": row.resource_id,
                "attempts": row.attempts,
                "expires_at": row.expires_at,
                "status": row.status,
                "error_code": row.error_code,
                "result": ManagementResult.model_validate(
                    self.store.cipher.decrypt_payload(row.encrypted_result)
                )
                if row.encrypted_result
                else None,
            }
        )

    def submit(
        self,
        channel_id: UUID,
        operation_id: UUID,
        origin: OperationOrigin,
        request: ManagementRequest,
    ) -> OperationReceipt:
        with self.store.sessions.begin() as session:
            return self.submit_in(session, channel_id, operation_id, origin, request)

    def submit_in(
        self,
        session: Session,
        channel_id: UUID,
        operation_id: UUID,
        origin: OperationOrigin,
        request: ManagementRequest,
        *,
        on_denied: Literal["raise", "record"] = "raise",
    ) -> OperationReceipt:
        channel = session.get(GatewayChannel, channel_id, with_for_update=True)
        if channel is None or channel.deleted:
            raise NotFoundError("Channel does not exist")
        definition = self.store.definition(channel)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "gateway-management:" + str(operation_id)},
        )
        fingerprint = payload_digest(
            {
                "channel": str(channel_id),
                "origin": origin.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
            }
        )
        previous = session.get(GatewayManagementOperation, operation_id)
        if previous is not None:
            if previous.channel_id != channel_id or previous.fingerprint != fingerprint:
                raise ConflictError("Management idempotency key was reused with different input")
            authorize_operation(
                session, self.store, definition, origin, request, validate_effect=False
            )
            return self.receipt(previous)
        denied = False
        try:
            authorize_operation(session, self.store, definition, origin, request)
        except ForbiddenError:
            if on_denied == "raise" or origin.kind not in {"filter", "event"}:
                raise
            denied = True
        group = definition.policy.groups.get(request.conversation.external_id)
        approval_actions = group.management.require_approval if group else frozenset()
        approval = origin.kind != "admin" and (
            request.action in approval_actions
            or (
                isinstance(request, ReviewJoinRequest)
                and request.blacklist
                and "blacklist" in approval_actions
            )
        )
        encrypted = self.store.cipher.encrypt_payload(
            {
                "request": request.model_dump(mode="json"),
            }
        )
        row = GatewayManagementOperation(
            id=operation_id,
            channel_id=channel_id,
            actor_id=origin.actor_id,
            source_kind=origin.kind,
            resource_id=origin.resource_id,
            generation=origin.generation,
            source_id=origin.source_id,
            fingerprint=fingerprint,
            adapter_generation=self.adapter_generation(definition.kind),
            channel_revision=channel.revision,
            binding_revision=channel.binding_revision,
            status="failed" if denied else "awaiting_approval" if approval else "pending",
            error_code="authorization_denied" if denied else None,
            attempts=0,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            encrypted_payload=encrypted.ciphertext,
            key_id=encrypted.key_id,
        )
        session.add(row)
        session.flush()
        self.store.audit(session, "management.submit", origin.actor_id, row.id)
        return self.receipt(row)

    def get(
        self, channel_id: UUID, operation_id: UUID, *, origin: OperationOrigin | None = None
    ) -> OperationReceipt:
        with self.store.sessions() as session:
            row = session.get(GatewayManagementOperation, operation_id)
            if row is None or row.channel_id != channel_id:
                raise NotFoundError("Management operation does not exist")
            if origin is not None:
                if (row.actor_id, row.resource_id) != (
                    origin.actor_id,
                    origin.resource_id,
                ):
                    raise NotFoundError("Management operation does not exist")
                channel = session.get(GatewayChannel, channel_id)
                if (
                    channel is None
                    or channel.deleted
                    or row.binding_revision != channel.binding_revision
                ):
                    raise ConflictError("The channel binding changed")
                authorize_operation(
                    session,
                    self.store,
                    self.store.definition(channel),
                    origin,
                    self.request(row),
                    validate_effect=False,
                )
            return self.receipt(row)

    def request(self, row: GatewayManagementOperation) -> ManagementRequest:
        return MANAGEMENT_REQUEST.validate_python(
            self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)["request"]
        )

    def list(self, channel_id: UUID) -> list[OperationReceipt]:
        with self.store.sessions() as session:
            return [
                self.receipt(row)
                for row in session.scalars(
                    select(GatewayManagementOperation)
                    .where(GatewayManagementOperation.channel_id == channel_id)
                    .order_by(GatewayManagementOperation.created_at.desc())
                    .limit(100)
                )
            ]

    def resolve(
        self,
        channel_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        action: Literal["approve", "cancel", "retry", "acknowledge"],
        *,
        accept_duplicate_risk: bool = False,
        result: ManagementResult | None = None,
    ) -> OperationReceipt:
        with self.store.sessions.begin() as session:
            channel = session.get(GatewayChannel, channel_id, with_for_update=True)
            row = session.get(GatewayManagementOperation, operation_id, with_for_update=True)
            if channel is None or channel.deleted or row is None or row.channel_id != channel_id:
                raise NotFoundError("Management operation does not exist")
            if action in {"approve", "retry"} and (
                row.channel_revision != channel.revision
                or row.binding_revision != channel.binding_revision
            ):
                raise ConflictError("Configuration changed; submit a newly authorized operation")
            if action in {"approve", "retry"} and row.expires_at <= datetime.now(UTC):
                raise ConflictError("The operation has expired")
            if action == "approve" and row.status == "awaiting_approval":
                row.approved_by, row.status = actor_id, "pending"
            elif action == "cancel" and row.status in {
                "pending",
                "awaiting_approval",
                "uncertain",
                "failed",
            }:
                row.status = "cancelled"
            elif action == "acknowledge" and row.status in {"uncertain", "failed"}:
                if result is None:
                    raise ConflictError("Acknowledgement requires a verified platform result")
                row.encrypted_result = self.store.cipher.encrypt_payload(
                    result.model_dump(mode="json")
                ).ciphertext
                row.status, row.error_code = "succeeded", None
            elif action == "retry" and row.status in {"failed", "uncertain"}:
                if (
                    row.status == "uncertain"
                    and self.request(row).action not in READ_ACTIONS
                    and not accept_duplicate_risk
                ):
                    raise ConflictError(
                        "Retrying an uncertain mutation requires accepting duplicate risk"
                    )
                row.status, row.error_code, row.retry_at = "pending", None, None
            else:
                raise ConflictError("The operation cannot perform this transition")
            self.store.audit(session, "management." + action, actor_id, row.id)
            return self.receipt(row)
