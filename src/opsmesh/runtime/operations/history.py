from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.operations.models import PlatformMetricSnapshot
from opsmesh.runtime.workers.models import WorkerNode
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
        interval = settings.platform_history_interval_seconds
        slot = int(now.timestamp()) // interval * interval
        if (
            self._session.scalar(
                select(PlatformMetricSnapshot.id).where(
                    PlatformMetricSnapshot.sample_slot == slot,
                )
            )
            is not None
        ):
            return
        snapshot = PlatformMetricSnapshot(sample_slot=slot, created_at=now, values={})
        try:
            with self._session.begin_nested():
                self._session.add(snapshot)
                self._session.flush([snapshot])
        except IntegrityError:
            return  # Another worker owns this sample interval.
        values: dict[str, object] = {}
        for name, model in (
            ("runs", AgentRun),
            ("workers", WorkerNode),
            ("runtimes", WorkspaceRuntime),
            ("approvals", Approval),
        ):
            rows = self._session.execute(
                select(model.status, func.count()).group_by(model.status)
            ).all()
            values[name] = {status: count for status, count in rows}
        snapshot.values = values
        self._session.execute(
            delete(PlatformMetricSnapshot).where(
                PlatformMetricSnapshot.created_at
                < now - timedelta(days=settings.platform_history_retention_days)
            )
        )

    def history(
        self,
        page: PageParams,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[list[PlatformMetricSnapshot], int]:
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
