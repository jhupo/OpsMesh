from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.metrics import GaugeMetric
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.operations.utils import ensure_aware_utc


class WorkerPrometheusMetrics:
    def __init__(self, session: Session) -> None:
        self._session = session

    def gauges(self, now: datetime, stale_after_seconds: int) -> list[GaugeMetric]:
        stale_cutoff = now - timedelta(seconds=stale_after_seconds)
        worker_states = {"online": 0, "offline": 0, "stale": 0}
        nodes = self._session.scalars(select(WorkerNode)).all()
        for node in nodes:
            if ensure_aware_utc(node.last_seen_at) < stale_cutoff:
                worker_states["stale"] += 1
            elif node.status == "online":
                worker_states["online"] += 1
            elif node.status == "offline":
                worker_states["offline"] += 1

        gauges = [
            GaugeMetric(
                "opsmesh_workers",
                count,
                labels={"state": state},
                help_text="Worker nodes by operational state.",
            )
            for state, count in sorted(worker_states.items())
        ]

        lease_counts = {
            status: int(count)
            for status, count in self._session.execute(
                select(WorkerLease.status, func.count())
                .where(WorkerLease.status.in_(["running", "failed"]))
                .group_by(WorkerLease.status)
            ).all()
        }
        gauges.extend(
            GaugeMetric(
                "opsmesh_worker_leases",
                lease_counts.get(status, 0),
                labels={"status": status},
                help_text="Worker leases by lifecycle status.",
            )
            for status in ("running", "failed")
        )
        return gauges
