"""Persist approved sandbox command requests and dispatch them through Temporal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.approvals.policy import ApprovalPolicyDecision, ApprovalPolicyEngine
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.runtime.commands.approvals import request_command_approval
from opsmesh.runtime.instances.events import new_runtime_event
from opsmesh.runtime.instances.models import RuntimeCommand, WorkspaceRuntime
from opsmesh.runtime.instances.security_events import RuntimeSecurityEventRecorder
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.config import Settings


class RuntimeCommandService:
    """Create a durable command record without executing user input in API/Worker."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def queue_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
        requested_by_user_id: UUID,
    ) -> RuntimeCommand:
        require_execution_admission(self._session)
        _validate_command(command)
        if runtime.workspace_id != workspace_id or runtime.status != "active":
            raise PermissionError("Execution environment is not active in this workspace")
        decision = self._evaluate(workspace_id, runtime, command)
        allowed = decision.decision.value == "allow"
        record = RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            requested_by_user_id=requested_by_user_id,
            status=(
                "queued" if allowed else "waiting_approval" if decision.required else "blocked"
            ),
            error=(
                None
                if allowed or decision.required
                else "runtime command denied by approval policy"
            ),
        )
        self._session.add(record)
        self._session.flush()
        self._session.add(
            new_runtime_event(
                workspace_id=workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=(
                    "runtime.command.queued"
                    if allowed
                    else "runtime.command.waiting_approval"
                    if decision.required
                    else "runtime.command.blocked"
                ),
                message=(
                    "Runtime command queued" if allowed else "Runtime command blocked by policy"
                ),
                metadata={
                    "runtime_id": str(runtime.id),
                    "command_id": str(record.id),
                    **_decision_metadata(command, decision),
                },
                created_at=datetime.now(UTC),
            )
        )
        if decision.required:
            request_command_approval(self._session, record)
        elif not allowed:
            self._record_blocked(runtime, command, decision)
        else:
            stage_execution(
                self._session,
                ExecutionRequest(
                    execution_id=record.id,
                    workspace_id=workspace_id,
                    resource_id=record.id,
                    kind=ExecutionKind.SANDBOX_CONTROL,
                    requested_by_user_id=requested_by_user_id,
                    parameters={"action": "execute"},
                ),
            )
        self._session.commit()
        self._session.refresh(record)
        return record

    def _evaluate(
        self, workspace_id: UUID, runtime: WorkspaceRuntime, command: list[str]
    ) -> ApprovalPolicyDecision:
        return ApprovalPolicyEngine(self._session, self._settings).evaluate_runtime_command(
            workspace_id=workspace_id,
            command=command,
            context={"runtime_id": str(runtime.id), "source": "runtime_control"},
        )

    def _record_blocked(
        self,
        runtime: WorkspaceRuntime,
        command: list[str],
        decision: ApprovalPolicyDecision,
    ) -> None:
        metadata = _decision_metadata(command, decision)
        RuntimeSecurityEventRecorder(self._session).record_command_blocked(
            runtime,
            reason="Runtime command was blocked by the approval policy",
            metadata=metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=runtime.workspace_id,
            action="runtime.command.blocked",
            target_type="workspace_runtime",
            target_id=runtime.id,
            metadata=metadata,
        )


def _validate_command(command: list[str]) -> None:
    if not command or len(command) > 32:
        raise ValueError("Runtime command must contain between 1 and 32 arguments")
    if any(not isinstance(item, str) or not item.strip() for item in command):
        raise ValueError("Runtime command arguments must be non-empty strings")
    if sum(len(item.encode("utf-8")) for item in command) > 16_384:
        raise ValueError("Runtime command exceeds its argument limit")


def _decision_metadata(command: list[str], decision: ApprovalPolicyDecision) -> dict[str, object]:
    payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
    return {
        "command_fingerprint": f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}",
        "argument_count": len(command),
        "policy_decision": decision.decision.value,
        "risk_level": decision.risk_level,
        "policy_reasons": list(decision.reasons),
    }
