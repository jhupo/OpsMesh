from __future__ import annotations

from uuid import UUID

from backend.app.orchestration.definitions.templates.normalization import (
    int_or_default,
    string_or_default,
    uuid_or_none,
)


def member_agent_profile_ids(members: list[dict[str, object]]) -> set[UUID]:
    return {
        agent_profile_id
        for member in members
        if (agent_profile_id := member_agent_profile_id(member)) is not None
    }


def first_member_agent_profile_id(members: list[dict[str, object]]) -> UUID | None:
    for member in members:
        agent_profile_id = member_agent_profile_id(member)
        if agent_profile_id is not None:
            return agent_profile_id
    return None


def member_agent_profile_id(member: dict[str, object] | None) -> UUID | None:
    if member is None:
        return None
    return uuid_or_none(member.get("agent_profile_id"))


def member_key(member: dict[str, object]) -> str:
    raw_id = member.get("id")
    if isinstance(raw_id, str) and raw_id:
        return f"id:{raw_id}"
    agent_profile_id = member_agent_profile_id(member)
    if agent_profile_id is not None:
        return f"agent:{agent_profile_id}"
    return f"role:{member_role(member, 'member')}:{member_department(member)}"


def member_role(member: dict[str, object], default: str) -> str:
    return string_or_default(member.get("team_role"), default)


def member_department(member: dict[str, object] | None) -> str | None:
    if member is None:
        return None
    department = member.get("department")
    return department if isinstance(department, str) and department else None


def member_position_title(member: dict[str, object]) -> str:
    return string_or_default(member.get("position_title"), "")


def request_department(request: dict[str, object]) -> str | None:
    for key in ("department", "required_department", "team", "area"):
        value = request.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def snapshot_members(snapshot: dict[str, object]) -> list[dict[str, object]]:
    raw_members = snapshot.get("members", [])
    if not isinstance(raw_members, list):
        return []
    members = [member for member in raw_members if isinstance(member, dict)]
    return sorted(
        members,
        key=lambda member: (
            int_or_default(member.get("order_index"), 0),
            str(member.get("team_role") or ""),
        ),
    )


def snapshot_agent_ids(snapshot: dict[str, object]) -> set[str]:
    team = snapshot.get("team") if isinstance(snapshot.get("team"), dict) else {}
    ids = {
        str(member["agent_profile_id"])
        for member in snapshot_members(snapshot)
        if member.get("agent_profile_id") is not None
    }
    if isinstance(team, dict) and team.get("manager_agent_profile_id") is not None:
        ids.add(str(team["manager_agent_profile_id"]))
    return ids
