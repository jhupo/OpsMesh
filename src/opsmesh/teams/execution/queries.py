"""Read persistent Team advancement states without transient queue introspection."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.shared.pagination import PageParams
from opsmesh.teams.execution.models import TeamOrchestrationRequest


def team_orchestration_requests(
    session: Session,
    workspace_id: UUID,
    team_id: UUID,
    page: PageParams,
    status: str | None,
) -> tuple[list[TeamOrchestrationRequest], int]:
    criteria = [
        TeamOrchestrationRequest.workspace_id == workspace_id,
        TeamOrchestrationRequest.team_id == team_id,
    ]
    if status is not None:
        criteria.append(TeamOrchestrationRequest.status == status)
    total = session.scalar(
        select(func.count()).select_from(TeamOrchestrationRequest).where(*criteria)
    )
    items = session.scalars(
        select(TeamOrchestrationRequest)
        .where(*criteria)
        .order_by(TeamOrchestrationRequest.created_at.desc(), TeamOrchestrationRequest.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return list(items), int(total or 0)


def team_orchestration_summary(
    session: Session,
    workspace_id: UUID,
    team_id: UUID,
) -> dict[str, object]:
    counts: dict[str, int] = {
        row[0]: row[1]
        for row in session.execute(
            select(TeamOrchestrationRequest.status, func.count())
            .where(
                TeamOrchestrationRequest.workspace_id == workspace_id,
                TeamOrchestrationRequest.team_id == team_id,
            )
            .group_by(TeamOrchestrationRequest.status)
        ).all()
    }
    return {
        "available": True,
        "mode": "temporal",
        **{
            status: int(counts.get(status, 0))
            for status in ("queued", "running", "completed", "failed", "unknown")
        },
    }
