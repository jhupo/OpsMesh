from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.audit.models import AuditIntegrityCheck
from backend.app.audit.service import AuditService
from backend.app.workspaces.models import Workspace


@dataclass(frozen=True)
class AuditIntegrityRunSummary:
    checked_workspaces: int
    valid_workspaces: int
    invalid_workspaces: int
    skipped_workspaces: int


class AuditIntegrityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def check_workspace(
        self,
        workspace_id: UUID,
        *,
        checked_at: datetime | None = None,
    ) -> AuditIntegrityCheck:
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
        return check

    def latest(self, workspace_id: UUID) -> AuditIntegrityCheck | None:
        return self._session.scalar(
            select(AuditIntegrityCheck)
            .where(AuditIntegrityCheck.workspace_id == workspace_id)
            .order_by(AuditIntegrityCheck.created_at.desc(), AuditIntegrityCheck.id.desc())
            .limit(1)
        )

    def run_due(
        self,
        *,
        interval_seconds: int,
        limit: int,
        now: datetime | None = None,
    ) -> AuditIntegrityRunSummary:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=interval_seconds)
        latest_checks = (
            select(
                AuditIntegrityCheck.workspace_id.label("workspace_id"),
                func.max(AuditIntegrityCheck.created_at).label("checked_at"),
            )
            .group_by(AuditIntegrityCheck.workspace_id)
            .subquery()
        )
        workspace_ids = list(
            self._session.scalars(
                select(Workspace.id)
                .outerjoin(
                    latest_checks,
                    latest_checks.c.workspace_id == Workspace.id,
                )
                .where(
                    or_(
                        latest_checks.c.checked_at.is_(None),
                        latest_checks.c.checked_at < cutoff,
                    )
                )
                .order_by(latest_checks.c.checked_at.asc().nullsfirst(), Workspace.id)
                .limit(limit)
            ).all()
        )
        total_workspaces = int(
            self._session.scalar(select(func.count()).select_from(Workspace)) or 0
        )
        valid = 0
        invalid = 0
        for workspace_id in workspace_ids:
            check = self.check_workspace(workspace_id, checked_at=now)
            if check.valid:
                valid += 1
            else:
                invalid += 1
        return AuditIntegrityRunSummary(
            checked_workspaces=valid + invalid,
            valid_workspaces=valid,
            invalid_workspaces=invalid,
            skipped_workspaces=max(0, total_workspaces - len(workspace_ids)),
        )
