"""Configuration invariants shared by team CRUD and metadata imports."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.references.schema import reject_embedded_secrets
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember


def validate_team_configuration(values: Mapping[str, object]) -> None:
    for key in ("coordination_rules", "default_task_policy"):
        if key not in values:
            continue
        value = values[key]
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        reject_embedded_secrets(value, path=key)


def validate_team_reporting_graph(reports_to_by_id: Mapping[UUID, UUID | None]) -> None:
    """Reject missing parents and cycles after import ID remapping or CRUD edits."""
    complete: set[UUID] = set()
    for member_id, parent_id in reports_to_by_id.items():
        if parent_id is not None and parent_id not in reports_to_by_id:
            raise ValueError("Reporting manager team member not found")
        seen: set[UUID] = set()
        current: UUID | None = member_id
        while current is not None and current not in complete:
            if current in seen:
                raise ValueError("Team member reporting line cannot contain a cycle")
            seen.add(current)
            current = reports_to_by_id.get(current)
        complete.update(seen)


def validate_team_reporting_line(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    member_id: UUID,
    reports_to_member_id: UUID | None,
) -> None:
    # Serialize graph edits while allowing FK key-share locks from newly added members.
    session.scalar(
        select(AgentTeam.id)
        .where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
        .with_for_update(key_share=True)
    )
    members = session.scalars(
        select(AgentTeamMember).where(
            AgentTeamMember.workspace_id == workspace_id,
            AgentTeamMember.agent_team_id == team_id,
        )
    ).all()
    graph = {member.id: member.reports_to_member_id for member in members}
    graph[member_id] = reports_to_member_id
    validate_team_reporting_graph(graph)
