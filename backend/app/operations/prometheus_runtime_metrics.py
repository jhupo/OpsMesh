from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.metrics import GaugeMetric
from backend.app.operations.observability_constants import ACTIVE_RUNTIME_RUN_STATUSES
from backend.app.operations.utils import capacity_slots_from_metadata
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpaceQuota
from backend.app.runtimes.models import WorkspaceRuntime


class RuntimePrometheusMetrics:
    def __init__(self, session: Session) -> None:
        self._session = session

    def gauges(self) -> list[GaugeMetric]:
        return [*self._runtime_capacity_gauges(), *self._runtime_quota_gauges()]

    def _runtime_capacity_gauges(self) -> list[GaugeMetric]:
        active_runs_by_runtime = dict(
            self._session.execute(
                select(AgentRun.runtime_id, func.count())
                .where(
                    AgentRun.runtime_id.is_not(None),
                    AgentRun.status.in_(ACTIVE_RUNTIME_RUN_STATUSES),
                )
                .group_by(AgentRun.runtime_id)
            )
            .tuples()
            .all()
        )
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for runtime in self._session.scalars(select(WorkspaceRuntime)).all():
            key = (runtime.runtime_provider, runtime.runtime_type)
            bucket = grouped.setdefault(key, {"capacity_slots": 0, "active_runs": 0})
            bucket["capacity_slots"] += capacity_slots_from_metadata(runtime.capabilities)
            bucket["active_runs"] += int(active_runs_by_runtime.get(runtime.id, 0))

        gauges: list[GaugeMetric] = []
        for (provider, runtime_type), values in sorted(grouped.items()):
            labels = {"provider": provider, "runtime_type": runtime_type}
            capacity_slots = values["capacity_slots"]
            active_runs = values["active_runs"]
            saturation = active_runs / capacity_slots if capacity_slots > 0 else 0.0
            gauges.extend(
                [
                    GaugeMetric(
                        "opsmesh_runtime_capacity_slots",
                        capacity_slots,
                        labels=labels,
                        help_text="Runtime capacity slots by provider and runtime type.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_active_runs",
                        active_runs,
                        labels=labels,
                        help_text="Active runs assigned to runtimes by provider and type.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_saturation_ratio",
                        round(saturation, 4),
                        labels=labels,
                        help_text="Runtime active-run saturation by provider and type.",
                    ),
                ]
            )
        return gauges

    def _runtime_quota_gauges(self) -> list[GaugeMetric]:
        quota_rows = self._session.execute(
            select(
                RuntimeSpaceQuota.quota_key,
                RuntimeSpaceQuota.unit,
                func.sum(RuntimeSpaceQuota.reserved_value),
                func.sum(RuntimeSpaceQuota.limit_value),
            )
            .where(RuntimeSpaceQuota.status == "active")
            .group_by(RuntimeSpaceQuota.quota_key, RuntimeSpaceQuota.unit)
        ).all()
        gauges: list[GaugeMetric] = []
        for quota_key, unit, reserved, limit in sorted(quota_rows):
            labels = {"quota_key": quota_key, "unit": unit}
            reserved_value = int(reserved or 0)
            limit_value = int(limit or 0)
            usage = reserved_value / limit_value if limit_value > 0 else 0.0
            gauges.extend(
                [
                    GaugeMetric(
                        "opsmesh_runtime_space_quota_reserved",
                        reserved_value,
                        labels=labels,
                        help_text="Reserved runtime space quota by quota key.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_space_quota_limit",
                        limit_value,
                        labels=labels,
                        help_text="Configured runtime space quota limit by quota key.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_space_quota_usage_ratio",
                        round(usage, 4),
                        labels=labels,
                        help_text="Runtime space quota usage ratio by quota key.",
                    ),
                ]
            )
        return gauges
