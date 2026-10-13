from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.telemetry.metrics import GaugeMetric


class TemporalPrometheusMetrics:
    """Expose durable Temporal outbox pressure without a second worker ledger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def gauges(self) -> list[GaugeMetric]:
        counts: dict[bool, int] = dict(
            self._session.execute(
                select(
                    TemporalOutboxEntry.published_at.is_(None).label("pending"),
                    func.count(),
                ).group_by(TemporalOutboxEntry.published_at.is_(None))
            )
            .tuples()
            .all()
        )
        pending = int(counts.get(True, 0))
        published = int(counts.get(False, 0))
        oldest_pending, failed_publications = self._session.execute(
            select(
                func.min(TemporalOutboxEntry.created_at),
                func.count().filter(TemporalOutboxEntry.last_error_code.is_not(None)),
            ).where(TemporalOutboxEntry.published_at.is_(None))
        ).one()
        oldest_age = (
            max(0.0, (datetime.now(UTC) - oldest_pending).total_seconds())
            if oldest_pending is not None
            else 0.0
        )
        return [
            GaugeMetric(
                "opsmesh_temporal_dispatches",
                pending,
                labels={"state": "pending"},
                help_text="Temporal outbox dispatches waiting for publication.",
            ),
            GaugeMetric(
                "opsmesh_temporal_dispatches",
                published,
                labels={"state": "published"},
                help_text="Temporal outbox dispatches already published.",
            ),
            GaugeMetric(
                "opsmesh_temporal_oldest_pending_dispatch_age_seconds",
                oldest_age,
                help_text="Age of the oldest unpublished transactional Temporal command.",
            ),
            GaugeMetric(
                "opsmesh_temporal_dispatch_publication_errors",
                int(failed_publications),
                help_text="Unpublished Temporal commands with a recorded publication failure.",
            ),
        ]
