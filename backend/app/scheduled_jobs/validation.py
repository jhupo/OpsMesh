from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from backend.app.model_providers.models import ModelProviderCredential
from backend.app.scheduled_jobs.constants import QUEUE_JOB_ACTION, RECORD_DUE_ACTION
from backend.app.scheduled_jobs.types import ScheduledJobCreate
from backend.app.workers.jobs import JobType


class ScheduledJobValidationMixin:
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
