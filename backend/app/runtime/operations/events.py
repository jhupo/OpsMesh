from __future__ import annotations

from typing import TypeVar
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.domains.capabilities.mcp.models import McpToolCallLog
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.observability.audit.models import AuditEvent
from backend.app.observability.audit.security_models import SecurityEvent
from backend.app.observability.audit.service import AuditService
from backend.app.observability.costs.models import ModelUsageRecord
from backend.app.runtime.environment.models import RuntimeEvent, WorkspaceRuntime
from backend.app.runtime.operations.contracts.events import OperationsCorrelationResponse
from backend.app.runtime.workers.models import WorkerLease

T = TypeVar("T")


class OperationsEventQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_run_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        event_type: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
    ) -> tuple[list[RunEvent], int]:
        statement = select(RunEvent).where(RunEvent.workspace_id == workspace_id)
        if event_type is not None:
            statement = statement.where(RunEvent.event_type == event_type)
        if trace_id is not None:
            statement = statement.where(RunEvent.trace_id == trace_id)
        if request_id is not None:
            statement = statement.where(RunEvent.request_id == request_id)
        return self._page(statement.order_by(RunEvent.created_at.desc()), page)

    def list_runtime_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        runtime_id: UUID | None = None,
        event_type: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
    ) -> tuple[list[RuntimeEvent], int]:
        statement = select(RuntimeEvent).where(RuntimeEvent.workspace_id == workspace_id)
        if runtime_id is not None:
            statement = statement.where(RuntimeEvent.workspace_runtime_id == runtime_id)
        if event_type is not None:
            statement = statement.where(RuntimeEvent.event_type == event_type)
        if trace_id is not None:
            statement = statement.where(RuntimeEvent.trace_id == trace_id)
        if request_id is not None:
            statement = statement.where(RuntimeEvent.request_id == request_id)
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
        trace_id: str | None = None,
        request_id: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        statement = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if target_type is not None:
            statement = statement.where(AuditEvent.target_type == target_type)
        if trace_id is not None:
            statement = statement.where(AuditEvent.trace_id == trace_id)
        if request_id is not None:
            statement = statement.where(AuditEvent.request_id == request_id)
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

    def correlation(
        self,
        workspace_id: UUID,
        *,
        trace_id: str | None,
        request_id: str | None,
    ) -> OperationsCorrelationResponse:
        if (trace_id is None) == (request_id is None):
            raise ValueError("Provide exactly one trace_id or request_id")
        models = (
            ("run_events", RunEvent),
            ("runtime_events", RuntimeEvent),
            ("worker_leases", WorkerLease),
            ("mcp_tool_calls", McpToolCallLog),
            ("audit_events", AuditEvent),
            ("model_attempts", ModelUsageRecord),
        )
        evidence_counts: dict[str, int] = {}
        rows_by_name: dict[str, list[object]] = {}
        for name, model in models:
            correlation_column = model.trace_id if trace_id is not None else model.request_id
            correlation_value = trace_id if trace_id is not None else request_id
            filters = (
                model.workspace_id == workspace_id,
                correlation_column == correlation_value,
            )
            evidence_counts[name] = int(
                self._session.scalar(select(func.count()).select_from(model).where(*filters))
                or 0
            )
            rows_by_name[name] = list(
                self._session.scalars(select(model).where(*filters).limit(100)).all()
            )

        rows = [row for values in rows_by_name.values() for row in values]
        trace_ids = _string_values(rows, "trace_id")
        request_ids = _string_values(rows, "request_id")
        run_ids = _run_ids(rows_by_name)
        worker_ids = _string_values(rows, "worker_id")
        runtime_ids = _runtime_ids(rows_by_name)
        run_ids.update(self._runtime_run_ids(workspace_id, runtime_ids))
        task_ids = _task_ids(rows)
        if run_ids:
            task_ids.update(
                task_id
                for task_id in self._session.scalars(
                    select(AgentRun.task_id).where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.id.in_(run_ids),
                        AgentRun.task_id.is_not(None),
                    )
                ).all()
                if task_id is not None
            )
        query = urlencode(
            {"trace_id": trace_id} if trace_id is not None else {"request_id": request_id}
        )
        base = f"/api/v1/workspaces/{workspace_id}"
        return OperationsCorrelationResponse(
            workspace_id=workspace_id,
            trace_ids=trace_ids,
            request_ids=request_ids,
            task_ids=sorted(task_ids, key=str),
            run_ids=sorted(run_ids, key=str),
            worker_ids=worker_ids,
            runtime_ids=runtime_ids,
            evidence_counts=evidence_counts,
            drilldowns={
                "run_events": f"{base}/operations/run-events?{query}",
                "runtime_events": f"{base}/operations/runtime-events?{query}",
                "mcp_tool_calls": f"{base}/capabilities/mcp-tool-call-logs?{query}",
                "audit_events": f"{base}/operations/audit-events?{query}",
                "model_attempts": f"{base}/costs/usage?{query}",
            },
        )

    def _runtime_run_ids(self, workspace_id: UUID, runtime_ids: list[str]) -> set[UUID]:
        workspace_runtime_ids = {
            value
            for runtime_id in runtime_ids
            if (value := _uuid_or_none(runtime_id)) is not None
        }
        if not workspace_runtime_ids:
            return set()
        return {
            run_id
            for run_id in self._session.scalars(
                select(WorkspaceRuntime.execution_run_id).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id.in_(workspace_runtime_ids),
                    WorkspaceRuntime.execution_run_id.is_not(None),
                )
            ).all()
            if run_id is not None
        }

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


def _string_values(rows: list[object], field: str) -> list[str]:
    return sorted(
        {
            value
            for row in rows
            if isinstance((value := getattr(row, field, None)), str) and value
        }
    )


def _run_ids(rows_by_name: dict[str, list[object]]) -> set[UUID]:
    values: set[UUID] = set()
    for name, rows in rows_by_name.items():
        field = "agent_run_id" if name != "worker_leases" else "resource_id"
        for row in rows:
            if name == "worker_leases" and getattr(row, "job_type", None) not in {
                "agent.run",
                "mcp.tool_execution",
            }:
                continue
            value = getattr(row, field, None)
            if isinstance(value, UUID):
                values.add(value)
    return values


def _task_ids(rows: list[object]) -> set[UUID]:
    return {
        value
        for row in rows
        if isinstance((value := getattr(row, "task_id", None)), UUID)
    }


def _runtime_ids(rows_by_name: dict[str, list[object]]) -> list[str]:
    values: set[str] = set()
    for name, rows in rows_by_name.items():
        for row in rows:
            value = (
                getattr(row, "workspace_runtime_id", None)
                if name == "runtime_events"
                else getattr(row, "runtime_id", None)
            )
            if value is not None:
                values.add(str(value))
    return sorted(values)


def _uuid_or_none(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None
