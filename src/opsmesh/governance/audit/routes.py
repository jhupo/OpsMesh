from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh.governance.audit.schemas import (
    AuditIntegrityStatusResponse,
    AuditIntegrityVerificationQueuedResponse,
)
from opsmesh.governance.audit.verification import AuditVerificationService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["audit"])


@router.get("/audit-integrity", response_model=AuditIntegrityStatusResponse)
def audit_integrity_status(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuditIntegrityStatusResponse:
    return AuditVerificationService(session).status(
        context.workspace.id, stale_after_seconds=settings.audit_integrity_stale_after_seconds
    )


@router.post(
    "/audit-integrity/verify",
    response_model=AuditIntegrityVerificationQueuedResponse,
    status_code=202,
)
def request_audit_verification(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> AuditIntegrityVerificationQueuedResponse:
    return AuditVerificationService(session).request(context.workspace.id, context.user.user_id)
