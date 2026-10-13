from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.shared.telemetry.metrics import GaugeMetric
from opsmesh.teams.execution.health import (
    team_runtime_health_for_metrics,
    team_runtime_metadata,
    team_runtime_workspace_runtime_id,
)
from opsmesh.teams.management.models import AgentTeam


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
        for team in teams:
            runtime_metadata = team_runtime_metadata(team)
            if not runtime_metadata:
                continue
            runtime_id = team_runtime_workspace_runtime_id(team)
            health = team_runtime_health_for_metrics(
                runtime_metadata=runtime_metadata,
                runtime=runtimes.get(runtime_id) if runtime_id is not None else None,
            )
            health_counts[health] = health_counts.get(health, 0) + 1
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
        ]
