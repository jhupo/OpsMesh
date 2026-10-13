from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.models import AuditIntegrityCheck
from opsmesh.governance.audit.service import AuditService
from opsmesh.messaging.notifications.service import GovernanceNotificationService
from opsmesh.workspaces.management.models import Workspace


class AuditIntegrityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def verify_and_record_workspace_integrity(
        self,
        workspace_id: UUID,
        *,
        checked_at: datetime | None = None,
    ) -> AuditIntegrityCheck:
        """Verify the retained audit chain and persist a check and notification.

        The check is flushed and the governance notification is created or
        updated in the borrowed Session. The caller owns commit and rollback.

        Args:
            workspace_id: Tenant whose retained hash chain is verified.
            checked_at: Optional timestamp for the persisted check.

        Returns:
            The persisted integrity result, including any broken event.

        Raises:
            ValueError: The workspace does not exist.
        """
        workspace_exists = self._session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id)
        )
        if workspace_exists is None:
            raise ValueError("Workspace not found")
        verification = AuditService(self._session).verify_workspace_hash_chain(workspace_id)
        check = AuditIntegrityCheck(
            workspace_id=workspace_id,
            checked_events=verification.checked_events,
            valid=verification.valid,
            broken_event_id=verification.broken_event_id,
            reason=verification.reason,
            created_at=checked_at or datetime.now(UTC),
        )
        self._session.add(check)
        self._session.flush([check])
        GovernanceNotificationService(self._session).record_audit_integrity(check)
        return check

    def latest(self, workspace_id: UUID) -> AuditIntegrityCheck | None:
        return self._session.scalar(
            select(AuditIntegrityCheck)
            .where(AuditIntegrityCheck.workspace_id == workspace_id)
            .order_by(AuditIntegrityCheck.created_at.desc(), AuditIntegrityCheck.id.desc())
            .limit(1)
        )
