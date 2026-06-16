from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.scheduled_jobs.audit import ScheduledJobAuditMixin
from backend.app.scheduled_jobs.dispatcher import ScheduledJobDispatcherMixin
from backend.app.scheduled_jobs.lifecycle import ScheduledJobLifecycleMixin
from backend.app.scheduled_jobs.maintenance import ScheduledJobMaintenanceMixin
from backend.app.scheduled_jobs.queries import ScheduledJobQueryMixin
from backend.app.scheduled_jobs.types import ScheduledJobMaintenanceSummary
from backend.app.scheduled_jobs.validation import ScheduledJobValidationMixin


class WorkspaceScheduledJobService(
    ScheduledJobAuditMixin,
    ScheduledJobQueryMixin,
    ScheduledJobValidationMixin,
    ScheduledJobDispatcherMixin,
    ScheduledJobLifecycleMixin,
    ScheduledJobMaintenanceMixin,
):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _maintenance_summary(
        self,
        *,
        enqueued: int,
        recorded: int,
        skipped: int,
        enqueued_by_job_type: dict[str, int],
        recorded_by_job_type: dict[str, int],
        skipped_by_job_type: dict[str, int],
    ) -> ScheduledJobMaintenanceSummary:
        return ScheduledJobMaintenanceSummary(
            enqueued=enqueued,
            recorded=recorded,
            skipped=skipped,
            enqueued_by_job_type=enqueued_by_job_type,
            recorded_by_job_type=recorded_by_job_type,
            skipped_by_job_type=skipped_by_job_type,
        )
