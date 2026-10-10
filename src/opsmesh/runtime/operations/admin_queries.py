from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.models import AuditIntegrityCheck
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.statuses import STALE_RECOVERABLE_RUN_STATUS_VALUES
from opsmesh.runtime.operations.models import AdminOperationRequest
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.utils import ensure_aware_utc
from opsmesh.workspaces.management.models import Workspace


class AdminDiagnosticsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runs(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None,
        kind: str,
        stale_after_seconds: int,
    ) -> tuple[list[AgentRun], int]:
        statement = select(AgentRun)
        if workspace_id is not None:
            statement = statement.where(AgentRun.workspace_id == workspace_id)
        if kind == "failed":
            statement = statement.where(AgentRun.status == "failed")
        else:
            anchor = case(
                (
                    AgentRun.status == "running",
                    func.coalesce(AgentRun.started_at, AgentRun.updated_at, AgentRun.created_at),
                ),
                else_=func.coalesce(AgentRun.updated_at, AgentRun.created_at),
            )
            statement = statement.where(
                AgentRun.status.in_(STALE_RECOVERABLE_RUN_STATUS_VALUES),
                anchor < datetime.now(UTC) - timedelta(seconds=stale_after_seconds),
            )
        return page_scalars(
            self._session, statement.order_by(AgentRun.updated_at, AgentRun.id), page
        )

    def workspace(self, workspace_id: UUID) -> Workspace:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        return workspace

    def request(self, request_id: UUID) -> AdminOperationRequest:
        row = self._session.get(AdminOperationRequest, request_id)
        if row is None:
            raise NotFoundError("Operation request not found")
        return row

    def requests(
        self, page: PageParams, *, workspace_id: UUID | None, status: str | None
    ) -> tuple[list[AdminOperationRequest], int]:
        statement = select(AdminOperationRequest)
        if workspace_id is not None:
            statement = statement.where(AdminOperationRequest.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AdminOperationRequest.status == status)
        return page_scalars(
            self._session,
            statement.order_by(AdminOperationRequest.created_at.desc(), AdminOperationRequest.id),
            page,
        )

    def integrity(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None,
        stale_after_seconds: int,
    ) -> tuple[list[dict[str, object]], int]:
        workspaces = select(Workspace)
        if workspace_id is not None:
            workspaces = workspaces.where(Workspace.id == workspace_id)
        rows, total = page_scalars(self._session, workspaces.order_by(Workspace.id), page)
        ids = [row.id for row in rows]
        ranked = (
            select(
                AuditIntegrityCheck.id,
                func.row_number()
                .over(
                    partition_by=AuditIntegrityCheck.workspace_id,
                    order_by=(AuditIntegrityCheck.created_at.desc(), AuditIntegrityCheck.id.desc()),
                )
                .label("position"),
            )
            .where(AuditIntegrityCheck.workspace_id.in_(ids))
            .subquery()
        )
        checks = self._session.scalars(
            select(AuditIntegrityCheck)
            .join(
                ranked,
                ranked.c.id == AuditIntegrityCheck.id,
            )
            .where(ranked.c.position == 1)
        ).all()
        latest = {check.workspace_id: check for check in checks}
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        result = []
        for workspace in rows:
            check = latest.get(workspace.id)
            stale = check is None or ensure_aware_utc(check.created_at) < cutoff
            result.append(
                {
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                    "status": "missing"
                    if check is None
                    else "invalid"
                    if not check.valid
                    else "stale"
                    if stale
                    else "valid",
                    "stale": stale,
                    "latest": check,
                }
            )
        return result, total
