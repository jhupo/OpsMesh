"""Durable schedule intent; native Temporal owns time and occurrence delivery."""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.scheduling.models import WorkspaceScheduledJob
from opsmesh.orchestration.scheduling.specs import build_schedule_spec
from opsmesh.orchestration.scheduling.types import (
    ACTIVE_STATUS,
    EXECUTE_ACTION,
    PAUSED_STATUS,
    RECORD_DUE_ACTION,
    ScheduledJobCreateCommand,
)
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.db.pagination import page_scalars_by_offset
from opsmesh.workspaces.management.models import Workspace


def validate_action(data: ScheduledJobCreateCommand) -> None:
    if data.action_type == RECORD_DUE_ACTION:
        if data.execution_kind is not None or data.resource_id is not None or data.parameters:
            raise ValueError("Recorded schedules cannot contain an execution")
        return
    if (
        data.action_type != EXECUTE_ACTION
        or data.execution_kind is None
        or data.resource_id is None
    ):
        raise ValueError("Execution schedules require an execution_kind and resource_id")
    if data.execution_kind in {
        ExecutionKind.SCHEDULE_CONTROL,
        ExecutionKind.SCHEDULE_TICK,
        ExecutionKind.CONVERSATION,
        ExecutionKind.ENTRY_EVENT,
        ExecutionKind.REPLY_DELIVERY,
    }:
        raise ValueError("This execution kind is not a schedulable product operation")
    ExecutionRequest(
        workspace_id=UUID(int=0),
        resource_id=data.resource_id,
        kind=data.execution_kind,
        parameters=data.parameters,
    )
    if data.execution_kind == ExecutionKind.MODEL_PROVIDER_HEALTH:
        validate_provider_health_parameters(data.parameters)


def validate_provider_health_parameters(parameters: Mapping[str, object]) -> None:
    probes = parameters.get("probes")
    if probes is not None and (
        not isinstance(probes, list)
        or not probes
        or any(item not in {"models", "inference"} for item in probes)
    ):
        raise ValueError("Health probes must contain models or inference")
    timeout = parameters.get("timeout_seconds")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int | float) or not 0 < timeout <= 60
    ):
        raise ValueError("Health timeout_seconds must be between 0 and 60")


def stage_schedule_control(
    session: Session, *, workspace_id: UUID, resource_id: UUID, owner_kind: str, user_id: UUID
) -> None:
    stage_execution(
        session,
        ExecutionRequest(
            workspace_id=workspace_id,
            resource_id=resource_id,
            kind=ExecutionKind.SCHEDULE_CONTROL,
            requested_by_user_id=user_id,
            parameters={"owner_kind": owner_kind},
        ),
    )


class WorkspaceScheduledJobService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        data: ScheduledJobCreateCommand,
    ) -> WorkspaceScheduledJob:
        validate_action(data)
        build_schedule_spec(data.schedule_type, data.schedule_config)
        if data.execution_kind == ExecutionKind.MODEL_PROVIDER_HEALTH:
            credential = self._session.scalar(
                select(ModelProviderCredential.id).where(
                    ModelProviderCredential.workspace_id == workspace.id,
                    ModelProviderCredential.id == data.resource_id,
                    ModelProviderCredential.status == "active",
                )
            )
            if credential is None:
                raise ValueError("Health schedule requires an active workspace credential")
        item = WorkspaceScheduledJob(
            workspace_id=workspace.id,
            created_by_user_id=user_id,
            name=data.name,
            schedule_type=data.schedule_type,
            schedule_config=data.schedule_config,
            status=ACTIVE_STATUS,
            action_type=data.action_type,
            execution_kind=data.execution_kind.value if data.execution_kind else None,
            resource_id=data.resource_id,
            parameters=data.parameters,
            metadata_=data.metadata,
            revision=1,
        )
        self._session.add(item)
        self._session.flush()
        self._stage(item, user_id, "workspace.scheduled_job.created")
        self._session.commit()
        self._session.refresh(item)
        return item

    def pause(
        self, *, workspace_id: UUID, scheduled_job_id: UUID, user_id: UUID
    ) -> WorkspaceScheduledJob:
        item = self._require(workspace_id, scheduled_job_id)
        if item.status == ACTIVE_STATUS:
            item.status = PAUSED_STATUS
            item.paused_at = datetime.now(UTC)
            item.next_run_at = None
            item.revision += 1
            self._stage(item, user_id, "workspace.scheduled_job.paused")
            self._session.commit()
            self._session.refresh(item)
        return item

    def resume(
        self,
        *,
        workspace_id: UUID,
        scheduled_job_id: UUID,
        user_id: UUID,
    ) -> WorkspaceScheduledJob:
        item = self._require(workspace_id, scheduled_job_id)
        if item.status == PAUSED_STATUS:
            if item.execution_kind is not None:
                ExecutionKind(item.execution_kind)
            item.status = ACTIVE_STATUS
            item.paused_at = None
            item.revision += 1
            self._stage(item, user_id, "workspace.scheduled_job.resumed")
            self._session.commit()
            self._session.refresh(item)
        return item

    def list_jobs(
        self, *, workspace_id: UUID, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[WorkspaceScheduledJob], int]:
        statement = select(WorkspaceScheduledJob).where(
            WorkspaceScheduledJob.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(WorkspaceScheduledJob.status == status)
        return page_scalars_by_offset(
            self._session,
            statement.order_by(
                WorkspaceScheduledJob.created_at.desc(), WorkspaceScheduledJob.id.desc()
            ),
            limit=limit,
            offset=offset,
        )

    def _require(self, workspace_id: UUID, scheduled_job_id: UUID) -> WorkspaceScheduledJob:
        item = self._session.scalar(
            select(WorkspaceScheduledJob)
            .where(
                WorkspaceScheduledJob.workspace_id == workspace_id,
                WorkspaceScheduledJob.id == scheduled_job_id,
            )
            .with_for_update()
        )
        if item is None:
            raise ValueError("Scheduled job not found")
        return item

    def _stage(self, item: WorkspaceScheduledJob, user_id: UUID, action: str) -> None:
        stage_schedule_control(
            self._session,
            workspace_id=item.workspace_id,
            resource_id=item.id,
            owner_kind="scheduled_job",
            user_id=user_id,
        )
        AuditService(self._session).record_user_action(
            workspace_id=item.workspace_id,
            user_id=user_id,
            action=action,
            target_type="workspace_scheduled_job",
            target_id=item.id,
            metadata={"revision": item.revision, "schedule_type": item.schedule_type},
        )
