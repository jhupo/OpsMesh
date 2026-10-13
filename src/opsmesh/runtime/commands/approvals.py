import json
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.approvals.service import ApprovalService
from opsmesh.runtime.instances.models import RuntimeCommand
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.workspaces.management.models import Workspace


def command_fingerprint(session: Session, record: RuntimeCommand) -> str:
    workspace = session.get(Workspace, record.workspace_id)
    if workspace is None:
        raise ValueError("Runtime command workspace not found")
    payload = [
        str(record.workspace_runtime_id),
        record.command,
        workspace.settings.get("approvals", {}),
    ]
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def command_approved(session: Session, record: RuntimeCommand) -> bool:
    fingerprint = command_fingerprint(session, record)
    rows = session.scalars(
        select(Approval).where(
            Approval.workspace_id == record.workspace_id,
            Approval.approval_type == "runtime.command.control",
            Approval.status == "approved",
        )
    )
    return any(
        a.payload.get("command_id") == str(record.id)
        and a.payload.get("fingerprint") == fingerprint
        for a in rows
    )


def request_command_approval(session: Session, record: RuntimeCommand) -> None:
    fingerprint = command_fingerprint(session, record)
    rows = session.scalars(
        select(Approval).where(
            Approval.workspace_id == record.workspace_id,
            Approval.approval_type == "runtime.command.control",
            Approval.status == "pending",
        )
    )
    if any(
        a.payload.get("command_id") == str(record.id)
        and a.payload.get("fingerprint") == fingerprint
        for a in rows
    ):
        return
    ApprovalService(session).create_approval(
        workspace_id=record.workspace_id,
        task_id=None,
        agent_run_id=None,
        requested_by_agent_profile_id=None,
        approval_type="runtime.command.control",
        risk_level="low",
        payload={
            "command_id": str(record.id),
            "runtime_id": str(record.workspace_runtime_id),
            "fingerprint": fingerprint,
            "command_preview": redact_sensitive_payload({"command": record.command})["command"],
        },
    )


def apply_command_decision(
    session: Session,
    approval: Approval,
    status: str,
) -> None:
    if approval.approval_type != "runtime.command.control":
        return
    record = session.scalar(
        select(RuntimeCommand)
        .where(
            RuntimeCommand.workspace_id == approval.workspace_id,
            RuntimeCommand.id == UUID(str(approval.payload["command_id"])),
        )
        .with_for_update()
    )
    if record is None or record.status != "waiting_approval":
        raise ValueError("Runtime command is no longer awaiting approval")
    if approval.payload.get("fingerprint") != command_fingerprint(session, record):
        raise ValueError("Runtime command or approval policy changed; resubmit the command")
    record.status = "queued" if status == "approved" else "blocked"
    if status == "approved":
        stage_execution(
            session,
            ExecutionRequest(
                execution_id=record.id,
                workspace_id=record.workspace_id,
                resource_id=record.id,
                kind=ExecutionKind.SANDBOX_CONTROL,
                requested_by_user_id=record.requested_by_user_id,
                parameters={"action": "execute"},
            ),
        )
