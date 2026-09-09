from __future__ import annotations

from uuid import UUID

from backend.app.planning.project_plan_members import (
    execution_members,
    executive_members,
    first_member_agent_profile_id,
    lead_members,
    manager_member,
    member_agent_profile_id,
    member_agent_profile_ids,
    snapshot_members,
)
from backend.app.planning.project_plan_utils import uuid_or_none
from backend.app.tasks.models import Task


class PlanningContext:
    def __init__(
        self,
        *,
        snapshot: dict[str, object],
        members: list[dict[str, object]],
        executives: list[dict[str, object]],
        manager: dict[str, object] | None,
        manager_planner_id: UUID | None,
        leads: list[dict[str, object]],
        execution_members_: list[dict[str, object]],
    ) -> None:
        self.snapshot = snapshot
        self.members = members
        self.executives = executives
        self.manager = manager
        self.manager_planner_id = manager_planner_id
        self.leads = leads
        self.execution_members = execution_members_

    @classmethod
    def from_task(cls, task: Task) -> PlanningContext | None:
        if not isinstance(task.team_snapshot, dict):
            return None
        snapshot = task.team_snapshot
        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None

        manager_agent_profile_id = uuid_or_none(team.get("manager_agent_profile_id"))
        members = snapshot_members(snapshot)
        if manager_agent_profile_id is None and not members:
            return None

        executives = executive_members(members)
        manager = manager_member(members, manager_agent_profile_id)
        executive_agent_ids = member_agent_profile_ids(executives)
        manager_planner_id = member_agent_profile_id(manager) or (
            manager_agent_profile_id
            if manager_agent_profile_id not in executive_agent_ids
            else None
        )
        leads = lead_members(members, manager_planner_id)
        return cls(
            snapshot=snapshot,
            members=members,
            executives=executives,
            manager=manager,
            manager_planner_id=manager_planner_id,
            leads=leads,
            execution_members_=execution_members(
                members,
                executives=executives,
                manager=manager,
                manager_agent_profile_id=manager_planner_id,
                leads=leads,
            ),
        )

    @property
    def planner_agent_profile_id(self) -> UUID | None:
        return self.manager_planner_id or first_member_agent_profile_id(
            self.executives or self.leads or self.execution_members,
        )
