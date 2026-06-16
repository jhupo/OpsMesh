from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.db.pagination import page_scalars_by_offset
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.scheduled_jobs.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace

ACTIVE_STATUS = "active"
PAUSED_STATUS = "paused"
COMPLETED_STATUS = "completed"
QUEUE_JOB_ACTION = "queue_job"
RECORD_DUE_ACTION = "record_due_action"


@dataclass(frozen=True)
class ScheduledJobMaintenanceSummary:
    enqueued: int = 0
    recorded: int = 0
    skipped: int = 0
    enqueued_by_job_type: dict[str, int] | None = None
    recorded_by_job_type: dict[str, int] | None = None
    skipped_by_job_type: dict[str, int] | None = None


@dataclass(frozen=True)
class ScheduledJobCreate:
    name: str
    schedule_type: str
    schedule_config: dict[str, object]
    action_type: str
    job_type: str | None
    resource_id: UUID | None
    routing: dict[str, object]
    priority: int
    max_attempts: int
    metadata: dict[str, object]


class WorkspaceScheduledJobService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        data: ScheduledJobCreate,
        now: datetime | None = None,
    ) -> WorkspaceScheduledJob:
        current_time = _utc(now)
        _validate_action(data)
        self._validate_workspace_resource(workspace.id, data)
        next_run_at = _next_run_at(
            schedule_type=data.schedule_type,
            schedule_config=data.schedule_config,
            after=current_time,
            include_now=True,
        )
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
            next_run_at=next_run_at,
        )
        self._session.add(scheduled_job)
        self._session.flush([scheduled_job])
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
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
        self._session.commit()
        self._session.refresh(scheduled_job)
        return scheduled_job

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
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=user_id,
                action="workspace.scheduled_job.paused",
                target_type="workspace_scheduled_job",
                target_id=scheduled_job.id,
                metadata={"name": scheduled_job.name},
            )
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
            current_time = _utc(now)
            scheduled_job.status = ACTIVE_STATUS
            scheduled_job.paused_at = None
            scheduled_job.next_run_at = _next_run_at(
                schedule_type=scheduled_job.schedule_type,
                schedule_config=scheduled_job.schedule_config,
                after=current_time,
                include_now=True,
            )
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
            self._session.commit()
            self._session.refresh(scheduled_job)
        return scheduled_job

    def enqueue_due(
        self,
        *,
        queue: RedisQueue | None,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ScheduledJobMaintenanceSummary:
        current_time = _utc(now)
        due_jobs = self._due_jobs(limit=limit, now=current_time)

        enqueued = 0
        recorded = 0
        skipped = 0
        enqueued_by_job_type: dict[str, int] = {}
        recorded_by_job_type: dict[str, int] = {}
        skipped_by_job_type: dict[str, int] = {}
        for scheduled_job in due_jobs:
            due_at = _utc(scheduled_job.next_run_at or current_time)
            event_status, queued_job_id, message = self._apply_due_action(
                scheduled_job,
                due_at=due_at,
                queue=queue,
            )
            job_type = scheduled_job.job_type or scheduled_job.action_type
            if event_status == "enqueued":
                enqueued += 1
                _increment(enqueued_by_job_type, job_type)
            elif event_status == "recorded":
                recorded += 1
                _increment(recorded_by_job_type, job_type)
            else:
                skipped += 1
                _increment(skipped_by_job_type, job_type)
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
        scheduled_job.next_run_at = _next_run_at(
            schedule_type=scheduled_job.schedule_type,
            schedule_config=scheduled_job.schedule_config,
            after=max(_utc(due_at), _utc(now)),
            include_now=False,
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

    def _validate_workspace_resource(
        self,
        workspace_id: UUID,
        data: ScheduledJobCreate,
    ) -> None:
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


def _validate_action(data: ScheduledJobCreate) -> None:
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
        _validate_provider_health_routing(data.routing)


def _validate_provider_health_routing(routing: dict[str, object]) -> None:
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


def _next_run_at(
    *,
    schedule_type: str,
    schedule_config: dict[str, object],
    after: datetime,
    include_now: bool,
) -> datetime:
    current_time = _utc(after)
    if schedule_type == "one_shot":
        run_at = _datetime_config(schedule_config, "run_at")
        if run_at is None:
            raise ValueError("One-shot scheduled jobs require schedule.run_at")
        return run_at
    if schedule_type == "hourly":
        minute = _int_config(schedule_config, "minute", default=0, minimum=0, maximum=59)
        candidate = current_time.replace(minute=minute, second=0, microsecond=0)
        if candidate < current_time or (candidate == current_time and not include_now):
            candidate += timedelta(hours=1)
        return candidate
    if schedule_type == "daily":
        daily_time = _time_config(schedule_config, "time_of_day")
        candidate = datetime.combine(current_time.date(), daily_time, tzinfo=UTC)
        if candidate < current_time or (candidate == current_time and not include_now):
            candidate += timedelta(days=1)
        return candidate
    raise ValueError("Scheduled job schedule_type must be one_shot, hourly, or daily")


def _increment(counts: dict[str, int], key: str | None) -> None:
    item = key or "unspecified"
    counts[item] = counts.get(item, 0) + 1


def _datetime_config(config: dict[str, object], key: str) -> datetime | None:
    value = config.get(key)
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str):
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    return None


def _time_config(config: dict[str, object], key: str) -> time:
    value = config.get(key)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Daily scheduled jobs require schedule.time_of_day as HH:MM") from exc
        return parsed.replace(tzinfo=None)
    raise ValueError("Daily scheduled jobs require schedule.time_of_day")


def _int_config(
    config: dict[str, object],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Scheduled job {key} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"Scheduled job {key} must be between {minimum} and {maximum}")
    return value


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
