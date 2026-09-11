from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import (
    AgentRuntimeContext,
    AgentRuntimeInterruption,
)
from backend.app.approvals.models import Approval
from backend.app.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from backend.app.approvals.service import ApprovalService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.redaction import redact_sensitive_payload


class AgentToolInterruptionService:
    def __init__(self, session: Session, secrets: SecretEncryptionService) -> None:
        self._session = session
        self._pending = PendingToolInvocationService(session, secrets)

    def persist(
        self,
        *,
        context: AgentRuntimeContext,
        requested_by_agent_profile_id: UUID | None,
        interruptions: tuple[AgentRuntimeInterruption, ...],
    ) -> None:
        for interruption in interruptions:
            idempotency_key = _idempotency_key(context, interruption)
            existing = self._pending.by_run_call(
                workspace_id=context.workspace_id,
                run_id=context.run_id,
                tool_call_id=interruption.tool_call_id,
            )
            approval = None
            if existing is not None:
                approval = existing.approval_id
            else:
                created = ApprovalService(self._session).create_approval(
                    workspace_id=context.workspace_id,
                    task_id=context.task_id,
                    agent_run_id=context.run_id,
                    requested_by_agent_profile_id=requested_by_agent_profile_id,
                    approval_type=f"{interruption.tool_kind}.tool",
                    risk_level=_risk_level(interruption.policy_decision),
                    payload={
                        "kind": "agent_tool_interruption",
                        "tool_call_id": interruption.tool_call_id,
                        "tool_name": interruption.tool_name,
                        "tool_kind": interruption.tool_kind,
                        "policy_decision": redact_sensitive_payload(
                            interruption.policy_decision
                        ),
                    },
                )
                approval = created.id
            invocation = self._pending.create_or_get(
                PendingToolInvocationRequest(
                    workspace_id=context.workspace_id,
                    task_id=context.task_id,
                    agent_run_id=context.run_id,
                    approval_id=approval,
                    tool_call_id=interruption.tool_call_id,
                    tool_name=interruption.tool_name,
                    tool_kind=interruption.tool_kind,
                    arguments=interruption.arguments,
                    policy_decision=interruption.policy_decision,
                    idempotency_key=idempotency_key,
                )
            )
            approval_model = self._session.get(Approval, approval)
            if approval_model is not None:
                approval_model.payload = {
                    **approval_model.payload,
                    "pending_tool_invocation_id": str(invocation.id),
                }


def _idempotency_key(
    context: AgentRuntimeContext,
    interruption: AgentRuntimeInterruption,
) -> str:
    call_digest = sha256(interruption.tool_call_id.encode("utf-8")).hexdigest()
    return f"sdk-tool:{context.run_id}:{call_digest}"


def _risk_level(policy_decision: dict[str, object]) -> str:
    value = str(policy_decision.get("risk_level") or "medium").lower()
    return value if value in {"low", "medium", "high", "critical"} else "medium"
