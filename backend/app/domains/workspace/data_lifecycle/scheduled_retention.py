"""Scheduled workspace retention execution."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_lifecycle.repository import WorkspaceDataLifecycleRepository
from backend.app.domains.workspace.data_lifecycle.retention import WorkspaceRetentionService
from backend.app.domains.workspace.data_lifecycle.scheduling import (
    ScheduledLifecycleSummary,
    backup_interval_hours,
    scheduled_lifecycle_detail,
)
from backend.app.domains.workspace.data_lifecycle.settings import (
    _bool_setting,
    _positive_int,
    _retention_settings,
)
from backend.app.domains.workspace.tenants.models import Workspace


class ScheduledRetentionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = WorkspaceDataLifecycleRepository(session)

    def run_if_due(
        self,
        workspace: Workspace,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _retention_settings(workspace.settings)
        if raw_policy.get("auto_apply") is not True:
            return ScheduledLifecycleSummary()

        interval_hours = backup_interval_hours(raw_policy)
        if interval_hours is None:
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.retention_skipped",
                reason="retention_schedule_unrecognized",
                metadata={"schedule": raw_policy.get("schedule")},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                retention_runs_skipped=1,
                details=[
                    scheduled_lifecycle_detail(
                        workspace.id,
                        "retention",
                        "skipped",
                        "retention_schedule_unrecognized",
                    )
                ],
            )

        latest_run_at = self._repository.latest_lifecycle_retention_run_at(workspace.id)
        now = datetime.now(UTC)
        if latest_run_at is not None and latest_run_at + timedelta(hours=interval_hours) > now:
            return ScheduledLifecycleSummary()


        response = WorkspaceRetentionService(self._session).apply_retention(
            workspace_id=workspace.id,
            user_id=workspace.owner_user_id,
            include_files=_bool_setting(raw_policy, "include_files", True),
            include_export_jobs=_bool_setting(raw_policy, "include_export_jobs", True),
            include_artifacts=_bool_setting(raw_policy, "include_artifacts", True),
            max_items=_positive_int(raw_policy.get("max_items")) or 100,
            require_successful_backup=_bool_setting(
                raw_policy,
                "require_successful_backup",
                True,
            ),
        )
        if response is None or response["blocked_reasons"]:
            blocked_reasons = (
                response["blocked_reasons"]
                if response is not None and isinstance(response["blocked_reasons"], list)
                else ["retention_response_missing"]
            )
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.retention_skipped",
                reason="retention_blocked",
                metadata={"blocked_reasons": blocked_reasons},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                retention_runs_skipped=1,
                details=[
                    scheduled_lifecycle_detail(
                        workspace.id,
                        "retention",
                        "skipped",
                        "retention_blocked",
                    )
                ],
            )
        return ScheduledLifecycleSummary(
            retention_runs_applied=1,
            details=[
                scheduled_lifecycle_detail(
                    workspace.id,
                    "retention",
                    "applied",
                    "retention_schedule_due",
                )
            ],
        )
