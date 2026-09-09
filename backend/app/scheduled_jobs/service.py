from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.scheduled_jobs.audit import ScheduledJobAuditMixin
from backend.app.scheduled_jobs.dispatcher import ScheduledJobDispatcherMixin
from backend.app.scheduled_jobs.lifecycle import ScheduledJobLifecycleMixin
from backend.app.scheduled_jobs.maintenance import ScheduledJobMaintenanceMixin
from backend.app.scheduled_jobs.queries import ScheduledJobQueryMixin
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
