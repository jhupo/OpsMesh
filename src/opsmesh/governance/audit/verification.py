"""Workspace audit verification admission and current evidence."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.governance.audit.schemas import (
    AuditIntegrityCheckResponse,
    AuditIntegrityStatusResponse,
    AuditIntegrityVerificationQueuedResponse,
)
from opsmesh.governance.audit.service import AuditService
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.utils import ensure_aware_utc


class AuditVerificationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def status(
        self, workspace_id: UUID, *, stale_after_seconds: int
    ) -> AuditIntegrityStatusResponse:
        latest = AuditIntegrityService(self._session).latest(workspace_id)
        stale = latest is None or ensure_aware_utc(latest.created_at) < datetime.now(
            UTC
        ) - timedelta(seconds=stale_after_seconds)
        return AuditIntegrityStatusResponse(
            status="missing"
            if latest is None
            else "invalid"
            if not latest.valid
            else "stale"
            if stale
            else "valid",
            stale=stale,
            latest=AuditIntegrityCheckResponse.model_validate(latest)
            if latest is not None
            else None,
        )

    def request(
        self, workspace_id: UUID, user_id: UUID
    ) -> AuditIntegrityVerificationQueuedResponse:
        require_execution_admission(self._session)
        request = ExecutionRequest(
            workspace_id=workspace_id,
            resource_id=workspace_id,
            kind=ExecutionKind.AUDIT_INTEGRITY,
            requested_by_user_id=user_id,
        )
        stage_execution(self._session, request)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="audit.integrity_verification_queued",
            target_type="workspace",
            target_id=workspace_id,
            metadata={"execution_id": str(request.execution_id)},
        )
        self._session.commit()
        return AuditIntegrityVerificationQueuedResponse(
            execution_id=request.execution_id, status="queued"
        )
