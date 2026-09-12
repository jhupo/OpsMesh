from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars_by_offset
from backend.app.domains.agents.providers.credentials.models import ModelProviderCredential
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit_service import AuditService
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.queue.redis import RedisQueue
from backend.app.runtime.workers.scheduling.calendar import next_run_at, utc_datetime
from backend.app.runtime.workers.scheduling.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)
from backend.app.runtime.workers.scheduling.types import (
    ACTIVE_STATUS,
    COMPLETED_STATUS,
    PAUSED_STATUS,
    QUEUE_JOB_ACTION,
    RECORD_DUE_ACTION,
    ScheduledJobCreate,
    ScheduledJobMaintenanceSummary,
)


def increment_count(counts: dict[str, int], key: str | None) -> None:
    item = key or "unspecified"
    counts[item] = counts.get(item, 0) + 1


def validate_action(data: ScheduledJobCreate) -> None:
    if data.action_type not in {QUEUE_JOB_ACTION, RECORD_DUE_ACTION}:
        raise ValueError("Scheduled job action_type must be queue_job or record_due_action")
    if data.action_type != QUEUE_JOB_ACTION:
        return
    if data.job_type is None:
        raise ValueError("Queue scheduled jobs require job_type")
    try:
        job_type = JobType(data.job_type)
    except ValueError as exc:
        raise ValueError("Queue scheduled jobs require a supported job_type") from exc
    if data.resource_id is None:
        raise ValueError("Queue scheduled jobs require resource_id")
    if job_type == JobType.MODEL_PROVIDER_HEALTH_CHECK:
        validate_provider_health_routing(data.routing)


def validate_provider_health_routing(routing: dict[str, object]) -> None:
    allowed_keys = {"probes", "timeout_seconds"}
    unknown = sorted(set(routing) - allowed_keys)
    if unknown:
        raise ValueError(
            "Model provider health check scheduled jobs only support probes and timeout_seconds"
        )
    probes = routing.get("probes")
    if probes is not None:
        if not isinstance(probes, list):
            raise ValueError("Model provider health check probes must be a list")
        parsed = tuple(dict.fromkeys(item for item in probes if isinstance(item, str) and item))
        if not parsed:
            raise ValueError("Model provider health check probes must include at least one probe")
        invalid = sorted(set(parsed) - {"models", "inference"})
        if invalid:
            raise ValueError(f"Unsupported provider health probe: {', '.join(invalid)}")
    timeout_seconds = routing.get("timeout_seconds")
    if timeout_seconds is not None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
            raise ValueError("Model provider health check timeout_seconds must be a number")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("Model provider health check timeout_seconds must be between 0 and 60")


class WorkspaceScheduledJobService:
    """Own schedule lifecycle, workspace validation and durable dispatch evidence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _record_created_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.scheduled_job.created",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={
                "name": scheduled_job.name,
                "schedule_type": scheduled_job.schedule_type,
                "schedule_config": scheduled_job.schedule_config,
                "action_type": scheduled_job.action_type,
                "job_type": scheduled_job.job_type,
                "metadata": scheduled_job.metadata_,
            },
        )

    def _record_paused_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.scheduled_job.paused",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={"name": scheduled_job.name},
        )

    def _record_resumed_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.scheduled_job.resumed",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={
                "name": scheduled_job.name,
                "next_run_at": scheduled_job.next_run_at.isoformat()
                if scheduled_job.next_run_at is not None
                else None,
            },
        )

    def _record_due_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        status: str,
        queued_job_id: UUID | None,
        message: str | None,
    ) -> None:
        actor_user_id = scheduled_job.created_by_user_id
        if actor_user_id is None:
            workspace = self._session.get(Workspace, scheduled_job.workspace_id)
            actor_user_id = workspace.owner_user_id if workspace is not None else None
        if actor_user_id is None:
            return
        AuditService(self._session).record_user_action(
            workspace_id=scheduled_job.workspace_id,
            user_id=actor_user_id,
            action="workspace.scheduled_job.due",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={
                "status": status,
                "queued_job_id": str(queued_job_id) if queued_job_id is not None else None,
                "message": message,
                "action_type": scheduled_job.action_type,
                "job_type": scheduled_job.job_type,
                "metadata": scheduled_job.metadata_,
            },
        )

    def _apply_due_action(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        due_at: datetime,
        queue: RedisQueue | None,
    ) -> tuple[str, UUID | None, str | None]:
        if scheduled_job.action_type == RECORD_DUE_ACTION:
            return "recorded", None, "Due action recorded"
        if queue is None:
            return "skipped", None, "Worker queue is unavailable"
        if scheduled_job.job_type is None or scheduled_job.resource_id is None:
            return "skipped", None, "Queue job action is incomplete"
        try:
            job_type = JobType(scheduled_job.job_type)
        except ValueError:
            return "skipped", None, "Queue job type is unsupported"
        queued_job = JobPayload(
            workspace_id=scheduled_job.workspace_id,
            job_type=job_type,
            resource_id=scheduled_job.resource_id,
            requested_by_user_id=scheduled_job.created_by_user_id,
            routing=scheduled_job.routing,
            priority=scheduled_job.priority,
            max_attempts=scheduled_job.max_attempts,
            idempotency_key=(
                "workspace.scheduled_job:"
                f"{scheduled_job.workspace_id}:{scheduled_job.id}:{due_at.isoformat()}"
            ),
        )
        if not queue.enqueue(queued_job):
            return "skipped", None, "Queue idempotency key already exists"
        return "enqueued", queued_job.job_id, None

    def create(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        data: ScheduledJobCreate,
        now: datetime | None = None,
    ) -> WorkspaceScheduledJob:
        current_time = utc_datetime(now)
        self._validate_create_data(workspace.id, data)
        scheduled_job = WorkspaceScheduledJob(
            workspace_id=workspace.id,
            created_by_user_id=user_id,
            name=data.name,
            schedule_type=data.schedule_type,
            schedule_config=data.schedule_config,
            status=ACTIVE_STATUS,
            action_type=data.action_type,
            job_type=data.job_type,
            resource_id=data.resource_id,
            routing=data.routing,
            priority=data.priority,
            max_attempts=data.max_attempts,
            metadata_=data.metadata,
            next_run_at=next_run_at(
                schedule_type=data.schedule_type,
                schedule_config=data.schedule_config,
                after=current_time,
                include_now=True,
            ),
        )
        self._session.add(scheduled_job)
        self._session.flush([scheduled_job])
        self._record_created_audit(scheduled_job, workspace_id=workspace.id, user_id=user_id)
        self._session.commit()
        self._session.refresh(scheduled_job)
        return scheduled_job

    def pause(
        self,
        *,
        workspace_id: UUID,
        scheduled_job_id: UUID,
        user_id: UUID,
    ) -> WorkspaceScheduledJob:
        scheduled_job = self._require_job(workspace_id, scheduled_job_id)
        if scheduled_job.status == ACTIVE_STATUS:
            scheduled_job.status = PAUSED_STATUS
            scheduled_job.paused_at = datetime.now(UTC)
            self._record_paused_audit(scheduled_job, workspace_id=workspace_id, user_id=user_id)
            self._session.commit()
            self._session.refresh(scheduled_job)
        return scheduled_job

    def resume(
        self,
        *,
        workspace_id: UUID,
        scheduled_job_id: UUID,
        user_id: UUID,
        now: datetime | None = None,
    ) -> WorkspaceScheduledJob:
        scheduled_job = self._require_job(workspace_id, scheduled_job_id)
        if scheduled_job.status == PAUSED_STATUS:
            current_time = utc_datetime(now)
            scheduled_job.status = ACTIVE_STATUS
            scheduled_job.paused_at = None
            scheduled_job.next_run_at = next_run_at(
                schedule_type=scheduled_job.schedule_type,
                schedule_config=scheduled_job.schedule_config,
                after=current_time,
                include_now=True,
            )
            self._record_resumed_audit(scheduled_job, workspace_id=workspace_id, user_id=user_id)
            self._session.commit()
            self._session.refresh(scheduled_job)
        return scheduled_job

    def _advance_schedule(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        due_at: datetime,
        now: datetime,
    ) -> None:
        scheduled_job.last_run_at = now
        if scheduled_job.schedule_type == "one_shot":
            scheduled_job.status = COMPLETED_STATUS
            scheduled_job.next_run_at = None
            scheduled_job.completed_at = now
            return
        scheduled_job.next_run_at = next_run_at(
            schedule_type=scheduled_job.schedule_type,
            schedule_config=scheduled_job.schedule_config,
            after=max(utc_datetime(due_at), utc_datetime(now)),
            include_now=False,
        )

    def enqueue_due(
        self,
        *,
        queue: RedisQueue | None,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ScheduledJobMaintenanceSummary:
        current_time = utc_datetime(now)
        due_jobs = self._due_jobs(limit=limit, now=current_time)

        enqueued = 0
        recorded = 0
        skipped = 0
        enqueued_by_job_type: dict[str, int] = {}
        recorded_by_job_type: dict[str, int] = {}
        skipped_by_job_type: dict[str, int] = {}
        for scheduled_job in due_jobs:
            due_at = utc_datetime(scheduled_job.next_run_at or current_time)
            event_status, queued_job_id, message = self._apply_due_action(
                scheduled_job,
                due_at=due_at,
                queue=queue,
            )
            job_type = scheduled_job.job_type or scheduled_job.action_type
            if event_status == "enqueued":
                enqueued += 1
                increment_count(enqueued_by_job_type, job_type)
            elif event_status == "recorded":
                recorded += 1
                increment_count(recorded_by_job_type, job_type)
            else:
                skipped += 1
                increment_count(skipped_by_job_type, job_type)
            self._advance_schedule(scheduled_job, due_at=due_at, now=current_time)
            self._session.add(
                WorkspaceScheduledJobEvent(
                    workspace_id=scheduled_job.workspace_id,
                    scheduled_job_id=scheduled_job.id,
                    due_at=due_at,
                    action_type=scheduled_job.action_type,
                    status=event_status,
                    queued_job_id=queued_job_id,
                    message=message,
                    metadata_={
                        "scheduled_job_name": scheduled_job.name,
                        "job_type": scheduled_job.job_type,
                        "resource_id": str(scheduled_job.resource_id)
                        if scheduled_job.resource_id is not None
                        else None,
                        "metadata": scheduled_job.metadata_,
                    },
                )
            )
            self._record_due_audit(
                scheduled_job,
                status=event_status,
                queued_job_id=queued_job_id,
                message=message,
            )

        if due_jobs:
            self._session.commit()
        return ScheduledJobMaintenanceSummary(
            enqueued=enqueued,
            recorded=recorded,
            skipped=skipped,
            enqueued_by_job_type=dict(sorted(enqueued_by_job_type.items())),
            recorded_by_job_type=dict(sorted(recorded_by_job_type.items())),
            skipped_by_job_type=dict(sorted(skipped_by_job_type.items())),
        )

    def list_jobs(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceScheduledJob], int]:
        statement = self._workspace_statement(workspace_id)
        if status is not None:
            statement = statement.where(WorkspaceScheduledJob.status == status)
        statement = statement.order_by(
            WorkspaceScheduledJob.created_at.desc(),
            WorkspaceScheduledJob.id.desc(),
        )
        return page_scalars_by_offset(self._session, statement, limit=limit, offset=offset)

    def _due_jobs(self, *, limit: int, now: datetime) -> list[WorkspaceScheduledJob]:
        statement = (
            select(WorkspaceScheduledJob)
            .where(
                WorkspaceScheduledJob.status == ACTIVE_STATUS,
                WorkspaceScheduledJob.next_run_at.is_not(None),
                WorkspaceScheduledJob.next_run_at <= now,
            )
            .order_by(WorkspaceScheduledJob.next_run_at.asc(), WorkspaceScheduledJob.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self._session.scalars(statement).all())

    def _workspace_statement(
        self,
        workspace_id: UUID,
    ) -> Select[tuple[WorkspaceScheduledJob]]:
        return select(WorkspaceScheduledJob).where(
            WorkspaceScheduledJob.workspace_id == workspace_id
        )

    def _require_job(
        self,
        workspace_id: UUID,
        scheduled_job_id: UUID,
    ) -> WorkspaceScheduledJob:
        scheduled_job = self._session.scalar(
            self._workspace_statement(workspace_id).where(
                WorkspaceScheduledJob.id == scheduled_job_id
            )
        )
        if scheduled_job is None:
            raise ValueError("Scheduled job not found")
        return scheduled_job

    def _validate_create_data(self, workspace_id: UUID, data: ScheduledJobCreate) -> None:
        validate_action(data)
        self._validate_workspace_resource(workspace_id, data)

    def _validate_workspace_resource(self, workspace_id: UUID, data: ScheduledJobCreate) -> None:
        if data.action_type != QUEUE_JOB_ACTION:
            return
        if data.job_type != JobType.MODEL_PROVIDER_HEALTH_CHECK.value:
            return
        credential = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == data.resource_id,
                ModelProviderCredential.status == "active",
            )
        )
        if credential is None:
            raise ValueError(
                "Model provider health check scheduled jobs require an active credential"
            )
