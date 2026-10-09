"""Explicit execution identities for product tests; no platform-selected default Agent."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.profiles.models import AgentProfile
from backend.app.orchestration.tasks.models import Task
from backend.app.teams.sessions.snapshots import build_team_snapshot


def test_agent_id(session: Session, workspace_id: UUID) -> UUID:
    profile = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == workspace_id, AgentProfile.name == "Fixture entry"
        )
    )
    if profile is None:
        profile = AgentProfile(
            workspace_id=workspace_id,
            name="Fixture entry",
            role="fixture",
            model="gpt-4.1",
            instructions="Execute the fixture's requested work.",
        )
        session.add(profile)
        session.flush()
    return profile.id


test_agent_id.__test__ = False


def author_fixture_plan(session: Session, task: Task) -> dict[str, object]:
    """Author the workflow used by scheduling fixtures, including explicit assignments."""
    snapshot = task.team_snapshot or build_team_snapshot(
        session, workspace_id=task.workspace_id, team_id=task.agent_team_id
    )
    task.team_snapshot = snapshot
    members = snapshot.get("members", [])
    manager_id = snapshot.get("team", {}).get("manager_agent_profile_id")
    packages = (task.input or {}).get("work_packages")
    if packages:
        packages = [dict(package) for package in packages]
        for package in packages:
            if package.get("assigned_agent_profile_id"):
                continue
            candidates = [
                member["agent_profile_id"]
                for member in members
                if not package.get("required_role")
                or member["team_role"] == package["required_role"]
            ]
            assert candidates, "Fixture must specify an eligible assignee"
            package["assigned_agent_profile_id"] = candidates[0]
    else:
        assert manager_id, "Fixture must configure its manager"
        packages = [
            {
                "package_id": "manager-planning",
                "title": "Manager planning",
                "assigned_agent_profile_id": manager_id,
                "required_role": "manager",
            }
        ]
        packages.extend(
            {
                "package_id": f"{member['team_role']}-{index + 1}",
                "title": f"{member['team_role']} execution",
                "required_role": member["team_role"],
                "assigned_agent_profile_id": member["agent_profile_id"],
                "depends_on": ["manager-planning"],
            }
            for index, member in enumerate(members)
            if member["agent_profile_id"] != manager_id
        )
        packages.append(
            {
                "package_id": "manager-summary",
                "title": "Manager summary",
                "required_role": "manager",
                "assigned_agent_profile_id": manager_id,
                "depends_on": [package["package_id"] for package in packages[1:]],
                "expected_artifacts": ["final_delivery"],
                "review_policy": {"reviewer": "user", "mode": "final_acceptance"},
            }
        )
    return {**(task.input or {}), "planning_mode": "explicit", "work_packages": packages}
