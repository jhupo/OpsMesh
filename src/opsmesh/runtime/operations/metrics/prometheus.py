from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from opsmesh.runtime.operations.metrics.governance import (
    GovernancePrometheusMetrics,
)
from opsmesh.runtime.operations.metrics.runtime import RuntimePrometheusMetrics
from opsmesh.runtime.operations.metrics.team_runtime import (
    TeamRuntimePrometheusMetrics,
)
from opsmesh.runtime.operations.metrics.temporal import TemporalPrometheusMetrics
from opsmesh.shared.telemetry.metrics import GaugeMetric


class OperationsMetricsCollector:
    def __init__(
        self,
        session: Session,
    ) -> None:
        self._session = session

    def prometheus_gauges(
        self,
        *,
        audit_integrity_stale_after_seconds: int = 7_200,
    ) -> list[GaugeMetric]:
        now = datetime.now(UTC)
        gauges: list[GaugeMetric] = []
        try:
            gauges.extend(TemporalPrometheusMetrics(self._session).gauges())
            gauges.extend(RuntimePrometheusMetrics(self._session).gauges())
            gauges.extend(TeamRuntimePrometheusMetrics(self._session).gauges(now))
            gauges.extend(
                GovernancePrometheusMetrics(self._session).gauges(
                    now,
                    audit_stale_after_seconds=audit_integrity_stale_after_seconds,
                )
            )
        except SQLAlchemyError:
            gauges.append(_collection_status("postgres", success=False))
        else:
            gauges.append(_collection_status("postgres", success=True))
        return gauges


def _collection_status(source: str, *, success: bool) -> GaugeMetric:
    return GaugeMetric(
        "opsmesh_metrics_collection_success",
        int(success),
        labels={"source": source},
        help_text="Whether the latest domain metrics collection succeeded.",
    )
