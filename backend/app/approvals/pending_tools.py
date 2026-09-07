from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.approvals.models import PendingToolInvocation
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.redaction import redact_sensitive_payload


@dataclass(frozen=True, slots=True)
class PendingToolInvocationRequest:
    workspace_id: UUID
    task_id: UUID | None
    agent_run_id: UUID
    approval_id: UUID
    tool_call_id: str
    tool_name: str
    tool_kind: str
    arguments: dict[str, object]
    policy_decision: dict[str, object]
    idempotency_key: str


class PendingToolInvocationService:
    def __init__(self, session: Session, secrets: SecretEncryptionService) -> None:
        self._session = session
        self._secrets = secrets

    def create_or_get(
        self,
        request: PendingToolInvocationRequest,
    ) -> PendingToolInvocation:
        _validate_request(request)
        encrypted = self._secrets.encrypt_payload(request.arguments)
        existing = self._by_idempotency_key(request.workspace_id, request.idempotency_key)
        if existing is not None:
            _require_same_invocation(existing, request, encrypted.fingerprint)
            return existing

        invocation = PendingToolInvocation(
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            agent_run_id=request.agent_run_id,
            approval_id=request.approval_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            tool_kind=request.tool_kind,
            encrypted_arguments=encrypted.ciphertext,
            arguments_fingerprint=encrypted.fingerprint,
            encryption_key_id=encrypted.key_id,
            policy_decision=_redacted_dict(request.policy_decision),
            idempotency_key=request.idempotency_key,
            status="pending",
        )
        try:
            with self._session.begin_nested():
                self._session.add(invocation)
                self._session.flush([invocation])
        except IntegrityError:
            existing = self._by_idempotency_key(request.workspace_id, request.idempotency_key)
            if existing is None:
                raise
            _require_same_invocation(existing, request, encrypted.fingerprint)
            return existing
        return invocation

    def arguments(
        self,
        *,
        workspace_id: UUID,
        invocation_id: UUID,
    ) -> dict[str, object]:
        invocation = self._session.scalar(
            select(PendingToolInvocation).where(
                PendingToolInvocation.workspace_id == workspace_id,
                PendingToolInvocation.id == invocation_id,
            )
        )
        if invocation is None:
            raise ValueError("Pending tool invocation not found")
        return self._secrets.decrypt_payload(
            invocation.encrypted_arguments,
            key_id=invocation.encryption_key_id,
        )

    def _by_idempotency_key(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> PendingToolInvocation | None:
        return self._session.scalar(
            select(PendingToolInvocation).where(
                PendingToolInvocation.workspace_id == workspace_id,
                PendingToolInvocation.idempotency_key == idempotency_key,
            )
        )


def _validate_request(request: PendingToolInvocationRequest) -> None:
    for name, value, maximum in (
        ("tool_call_id", request.tool_call_id, 255),
        ("tool_name", request.tool_name, 255),
        ("tool_kind", request.tool_kind, 32),
        ("idempotency_key", request.idempotency_key, 255),
    ):
        if not value or len(value) > maximum:
            raise ValueError(f"{name} must contain between 1 and {maximum} characters")


def _require_same_invocation(
    existing: PendingToolInvocation,
    request: PendingToolInvocationRequest,
    arguments_fingerprint: str,
) -> None:
    if (
        existing.agent_run_id != request.agent_run_id
        or existing.approval_id != request.approval_id
        or existing.tool_call_id != request.tool_call_id
        or existing.tool_name != request.tool_name
        or existing.tool_kind != request.tool_kind
        or existing.arguments_fingerprint != arguments_fingerprint
    ):
        raise ValueError("Idempotency key is already bound to another tool invocation")


def _redacted_dict(value: dict[str, object]) -> dict[str, object]:
    redacted = redact_sensitive_payload(value)
    return redacted if isinstance(redacted, dict) else {}
