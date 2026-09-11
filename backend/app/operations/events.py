from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.observability.audit_models import AuditEvent
from backend.app.observability.audit_service import AuditService
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_manager.models import RuntimeEvent
from backend.app.security.models import SecurityEvent

T = TypeVar("T")


class OperationsEventQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_run_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        event_type: str | None = None,
    ) -> tuple[list[RunEvent], int]:
        statement = select(RunEvent).where(RunEvent.workspace_id == workspace_id)
        if event_type is not None:
            statement = statement.where(RunEvent.event_type == event_type)
        return self._page(statement.order_by(RunEvent.created_at.desc()), page)

    def list_runtime_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        runtime_id: UUID | None = None,
        event_type: str | None = None,
    ) -> tuple[list[RuntimeEvent], int]:
        statement = select(RuntimeEvent).where(RuntimeEvent.workspace_id == workspace_id)
        if runtime_id is not None:
            statement = statement.where(RuntimeEvent.workspace_runtime_id == runtime_id)
        if event_type is not None:
            statement = statement.where(RuntimeEvent.event_type == event_type)
        return self._page(statement.order_by(RuntimeEvent.created_at.desc()), page)

    def inspect_failed_runs(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentRun], int]:
        statement = (
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.status == "failed")
            .order_by(AgentRun.updated_at.desc())
        )
        return self._page(statement, page)

    def filter_audit_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        action: str | None = None,
        target_type: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        statement = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if target_type is not None:
            statement = statement.where(AuditEvent.target_type == target_type)
        statement = AuditService(self._session).apply_retention_to_statement(statement)
        return self._page(statement.order_by(AuditEvent.created_at.desc()), page)

    def filter_security_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        action: str | None = None,
        severity: str | None = None,
        user_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], int]:
        statement = select(SecurityEvent).where(SecurityEvent.workspace_id == workspace_id)
        if action is not None:
            statement = statement.where(SecurityEvent.action == action)
        if severity is not None:
            statement = statement.where(SecurityEvent.severity == severity)
        if user_id is not None:
            statement = statement.where(SecurityEvent.user_id == user_id)
        return self._page(statement.order_by(SecurityEvent.created_at.desc()), page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
