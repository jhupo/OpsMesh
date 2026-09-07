from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeApprovalDecision
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

    def by_run_call(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        tool_call_id: str,
    ) -> PendingToolInvocation | None:
        return self._session.scalar(
            select(PendingToolInvocation).where(
                PendingToolInvocation.workspace_id == workspace_id,
                PendingToolInvocation.agent_run_id == run_id,
                PendingToolInvocation.tool_call_id == tool_call_id,
            )
        )

    def by_approval(
        self,
        *,
        workspace_id: UUID,
        approval_id: UUID,
    ) -> PendingToolInvocation | None:
        return self._session.scalar(
            select(PendingToolInvocation).where(
                PendingToolInvocation.workspace_id == workspace_id,
                PendingToolInvocation.approval_id == approval_id,
            )
        )

    def record_decision(
        self,
        *,
        workspace_id: UUID,
        approval_id: UUID,
        status: str,
    ) -> PendingToolInvocation | None:
        if status not in {"approved", "rejected"}:
            raise ValueError("Pending tool decision is invalid")
        invocation = self.by_approval(
            workspace_id=workspace_id,
            approval_id=approval_id,
        )
        if invocation is None:
            return None
        if invocation.status != "pending":
            raise ValueError("Pending tool invocation is not awaiting a decision")
        invocation.status = status
        self._session.flush([invocation])
        return invocation

    def decisions_for_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> tuple[AgentRuntimeApprovalDecision, ...]:
        from backend.app.approvals.models import Approval

        rows = self._session.execute(
            select(PendingToolInvocation, Approval)
            .join(Approval, Approval.id == PendingToolInvocation.approval_id)
            .where(
                PendingToolInvocation.workspace_id == workspace_id,
                PendingToolInvocation.agent_run_id == run_id,
                PendingToolInvocation.status.in_(("approved", "rejected")),
            )
            .order_by(PendingToolInvocation.created_at, PendingToolInvocation.id)
        ).all()
        return tuple(
            AgentRuntimeApprovalDecision(
                tool_call_id=invocation.tool_call_id,
                tool_name=invocation.tool_name,
                status=invocation.status,
                reason=approval.decision_reason,
            )
            for invocation, approval in rows
        )

    def claim_execution(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> tuple[PendingToolInvocation, dict[str, object] | None]:
        invocation = self._session.scalar(
            select(PendingToolInvocation)
            .where(
                PendingToolInvocation.workspace_id == workspace_id,
                PendingToolInvocation.agent_run_id == run_id,
                PendingToolInvocation.tool_call_id == tool_call_id,
            )
            .with_for_update()
        )
        if invocation is None:
            raise ValueError("Approved tool invocation not found")
        fingerprint = self._secrets.encrypt_payload(arguments).fingerprint
        if (
            invocation.tool_name != tool_name
            or invocation.arguments_fingerprint != fingerprint
        ):
            raise ValueError("Approved tool invocation does not match the SDK tool call")
        if invocation.status in {"completed", "failed"}:
            return invocation, self._stored_result(invocation)
        if invocation.status == "executing":
            return invocation, {
                "status": "failed",
                "error": {
                    "code": "tool_execution_outcome_unknown",
                    "message": (
                        "The approved tool call was already claimed and will not be replayed"
                    ),
                },
                "metadata": {"idempotency_key": invocation.idempotency_key},
            }
        if invocation.status != "approved":
            raise ValueError("Tool invocation has not been approved")
        invocation.status = "executing"
        invocation.attempt_count += 1
        invocation.execution_started_at = datetime.now(UTC)
        self._session.commit()
        return invocation, None

    def complete_execution(
        self,
        invocation: PendingToolInvocation,
        result: dict[str, object],
    ) -> None:
        encrypted = self._secrets.encrypt_payload(result)
        invocation.encrypted_result = encrypted.ciphertext
        invocation.result_fingerprint = encrypted.fingerprint
        invocation.result_encryption_key_id = encrypted.key_id
        invocation.status = "completed" if result.get("status") == "completed" else "failed"
        invocation.completed_at = datetime.now(UTC)
        self._session.commit()

    def _stored_result(self, invocation: PendingToolInvocation) -> dict[str, object]:
        if invocation.encrypted_result is None or invocation.result_encryption_key_id is None:
            raise ValueError("Stored tool execution result is missing")
        return self._secrets.decrypt_payload(
            invocation.encrypted_result,
            key_id=invocation.result_encryption_key_id,
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
