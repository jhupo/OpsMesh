from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.metrics import GaugeMetric
from backend.app.operations.team_runtime_health import (
    team_runtime_health_for_metrics,
    team_runtime_metadata,
    team_runtime_workspace_runtime_id,
)
from backend.app.operations.utils import non_negative_int
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam


class TeamRuntimePrometheusMetrics:
    def __init__(self, session: Session) -> None:
        self._session = session

    def gauges(self, now: datetime) -> list[GaugeMetric]:
        teams = self._session.scalars(select(AgentTeam).where(AgentTeam.status == "active")).all()
        runtime_ids = {
            runtime_id
            for team in teams
            for runtime_id in [team_runtime_workspace_runtime_id(team)]
            if runtime_id is not None
        }
        runtimes = (
            {
                runtime.id: runtime
                for runtime in self._session.scalars(
                    select(WorkspaceRuntime).where(WorkspaceRuntime.id.in_(runtime_ids))
                ).all()
            }
            if runtime_ids
            else {}
        )
        health_counts = {
            "starting": 0,
            "healthy": 0,
            "stale": 0,
            "degraded": 0,
            "paused": 0,
            "stopped": 0,
        }
        iteration_count = 0
        scheduled_loop_enabled = 0
        for team in teams:
            runtime_metadata = team_runtime_metadata(team)
            if not runtime_metadata:
                continue
            runtime_id = team_runtime_workspace_runtime_id(team)
            health = team_runtime_health_for_metrics(
                runtime_metadata=runtime_metadata,
                runtime=runtimes.get(runtime_id) if runtime_id is not None else None,
                generated_at=now,
            )
            health_counts[health] = health_counts.get(health, 0) + 1
            iteration_count += non_negative_int(runtime_metadata.get("iteration_count"))
            scheduling_policy = runtime_metadata.get("scheduling_policy")
            if not isinstance(scheduling_policy, dict) or (
                scheduling_policy.get("scheduled_loop_enabled") is not False
            ):
                scheduled_loop_enabled += 1
        return [
            *[
                GaugeMetric(
                    "opsmesh_team_runtimes",
                    count,
                    labels={"health": health},
                    help_text="Team runtimes by low-cardinality runtime health.",
                )
                for health, count in sorted(health_counts.items())
            ],
            GaugeMetric(
                "opsmesh_team_runtime_iterations_total",
                iteration_count,
                labels={},
                help_text="Total persisted team runtime iterations across active teams.",
            ),
            GaugeMetric(
                "opsmesh_team_runtime_scheduled_loops",
                scheduled_loop_enabled,
                labels={"state": "enabled"},
                help_text="Active team runtimes with scheduled loop cadence enabled.",
            ),
        ]
