from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.platform.operations.models import PlatformMetricSnapshot
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.pagination import PageParams


class PlatformHistoryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def capture(self, settings: Settings, *, now: datetime | None = None) -> None:
        if not settings.platform_history_enabled:
            return
        now = now or datetime.now(UTC)
        slot = int(now.timestamp()) // settings.platform_history_interval_seconds
        slot *= settings.platform_history_interval_seconds
        if self._session.scalar(
            select(PlatformMetricSnapshot.id).where(PlatformMetricSnapshot.sample_slot == slot)
        ):
            return
        snapshot = PlatformMetricSnapshot(sample_slot=slot, created_at=now, values={})
        try:
            with self._session.begin_nested():
                self._session.add(snapshot)
                self._session.flush([snapshot])
        except IntegrityError:
            return
        values: dict[str, object] = {}
        for name, model in (
            ("runs", AgentRun),
            ("runtimes", WorkspaceRuntime),
            ("approvals", Approval),
        ):
            values[name] = dict(
                self._session.execute(select(model.status, func.count()).group_by(model.status))
                .tuples()
                .all()
            )
        values["temporal_dispatch"] = {
            "pending": self._session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(TemporalOutboxEntry.published_at.is_(None))
            )
            or 0
        }
        snapshot.values = values
        self._session.execute(
            delete(PlatformMetricSnapshot).where(
                PlatformMetricSnapshot.created_at
                < now - timedelta(days=settings.platform_history_retention_days)
            )
        )

    def history(
        self, page: PageParams, *, start_at: datetime, end_at: datetime
    ) -> tuple[list[PlatformMetricSnapshot], int]:
        if start_at.tzinfo is None or end_at.tzinfo is None or start_at >= end_at:
            raise ValueError("History requires an ordered timezone-aware time window")
        return page_scalars(
            self._session,
            select(PlatformMetricSnapshot)
            .where(
                PlatformMetricSnapshot.created_at >= start_at,
                PlatformMetricSnapshot.created_at < end_at,
            )
            .order_by(PlatformMetricSnapshot.created_at, PlatformMetricSnapshot.id),
            page,
        )
