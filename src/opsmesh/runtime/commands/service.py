import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.approvals.policy import ApprovalPolicyDecision, ApprovalPolicyEngine
from opsmesh.runtime.commands.approvals import command_approved, request_command_approval
from opsmesh.runtime.instances.contracts import RuntimeManagerProvider
from opsmesh.runtime.instances.events import new_runtime_event
from opsmesh.runtime.instances.models import RuntimeCommand, WorkspaceRuntime
from opsmesh.runtime.instances.queries import RuntimeControlQueryService
from opsmesh.runtime.instances.security_events import RuntimeSecurityEventRecorder
from opsmesh.shared.config import Settings


class RuntimeCommandService:
    def __init__(
        self,
        session: Session,
        manager_factory: RuntimeManagerProvider,
        settings: Settings,
    ) -> None:
        self._session = session
        self._manager_factory = manager_factory
        self._settings = settings

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        _validate_command(command)
        decision = self._evaluate(workspace_id, runtime, command, source="runtime_control")
        if decision.decision.value != "allow":
            raise PermissionError("runtime command denied by approval policy")
        return self._manager_factory.require().execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )

    def queue_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        _validate_command(command)
        decision = self._evaluate(workspace_id, runtime, command, source="runtime_control_queue")
        allowed = decision.decision.value == "allow"
        record = RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            status="queued" if allowed else "waiting_approval" if decision.required else "blocked",
            error=None
            if allowed or decision.required
            else "runtime command denied by approval policy",
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
        self._session.commit()
        self._session.refresh(record)
        return record

    def execute_queued_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        runtime_id: UUID,
        command_id: UUID,
        command: list[str],
    ) -> RuntimeCommand | None:
        record = RuntimeControlQueryService(self._session).get_command(
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            command_id=command_id,
        )
        if record is None:
            return None
        if record.status != "queued":
            return record
        if record.command != command or record.workspace_runtime_id != runtime.id:
            self._block_record(
                runtime,
                record,
                command,
                "runtime command payload does not match the queued command",
            )
            return record
        decision = self._evaluate(workspace_id, runtime, command, source="runtime_control_queue")
        if decision.required and not command_approved(self._session, record):
            record.status = "waiting_approval"
            request_command_approval(self._session, record)
            self._session.commit()
            return record
        if decision.blocked:
            self._block_record(
                runtime,
                record,
                command,
                "runtime command denied by approval policy",
                decision=decision,
            )
            self._session.commit()
            return record
        return self._manager_factory.require().execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )

    def _evaluate(
        self,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
        *,
        source: str,
    ) -> ApprovalPolicyDecision:
        return ApprovalPolicyEngine(self._session, self._settings).evaluate_runtime_command(
            workspace_id=workspace_id,
            command=command,
            context={"runtime_id": str(runtime.id), "source": source},
        )

    def _record_blocked(
        self,
        runtime: WorkspaceRuntime,
        command: list[str],
        decision: ApprovalPolicyDecision | None,
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

    def _block_record(
        self,
        runtime: WorkspaceRuntime,
        record: RuntimeCommand,
        command: list[str],
        error: str,
        *,
        decision: ApprovalPolicyDecision | None = None,
    ) -> None:
        record.status = "blocked"
        record.error = error
        metadata = _decision_metadata(command, decision)
        metadata["command_id"] = str(record.id)
        self._session.add(
            new_runtime_event(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.command.blocked",
                message="Runtime command blocked",
                metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )
        self._record_blocked(runtime, command, decision)
        self._session.commit()


def _validate_command(command: list[str]) -> None:
    if not command or len(command) > 32:
        raise ValueError("Runtime command must contain between 1 and 32 arguments")
    if any(not isinstance(item, str) or not item.strip() for item in command):
        raise ValueError("Runtime command arguments must be non-empty strings")
    if sum(len(item.encode("utf-8")) for item in command) > 16_384:
        raise ValueError("Runtime command exceeds its argument limit")


def _decision_metadata(
    command: list[str],
    decision: ApprovalPolicyDecision | None,
) -> dict[str, object]:
    payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
    return {
        "command_fingerprint": f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}",
        "argument_count": len(command),
        "policy_decision": decision.decision.value if decision is not None else "deny",
        "risk_level": decision.risk_level if decision is not None else "critical",
        "policy_reasons": list(decision.reasons) if decision is not None else [],
    }
